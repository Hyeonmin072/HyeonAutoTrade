"""
거래소 어댑터 모듈
ccxt 래퍼 - 통일된 거래소 인터페이스
"""
import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime
from dataclasses import dataclass, field

import ccxt

from ..monitoring.logger import get_logger


logger = get_logger("exchange_adapter")


@dataclass
class Balance:
    """잔고 정보"""
    total: float
    free: float
    used: float
    currency: str
    
    @property
    def locked(self) -> bool:
        return self.free <= 0


@dataclass
class Order:
    """주문 정보"""
    id: str
    symbol: str
    type: str  # market, limit
    side: str  # buy, sell
    price: float
    amount: float
    filled: float
    remaining: float
    status: str  # open, filled, cancelled
    timestamp: datetime
    fee: float = 0.0
    fee_currency: str = ""
    
    @property
    def filled_percent(self) -> float:
        if self.amount == 0:
            return 0
        return self.filled / self.amount * 100
    
    @property
    def is_filled(self) -> bool:
        return self.status == "filled" or self.filled_percent >= 99.9
    
    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "type": self.type,
            "side": self.side,
            "price": self.price,
            "amount": self.amount,
            "filled": self.filled,
            "remaining": self.remaining,
            "status": self.status,
            "timestamp": self.timestamp.isoformat(),
            "fee": self.fee,
            "fee_currency": self.fee_currency,
            "filled_percent": self.filled_percent
        }


class ExchangeAdapter:
    """
    거래소 어댑터
    ccxt 라이브러리 래퍼
    바이낸스: spot(현물) / futures(USDT선물) 지원
    """
    
    def __init__(
        self,
        exchange_name: str,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        testnet: bool = False,
        options: Optional[Dict] = None,
        mode: str = "spot",
        leverage: int = 1
    ):
        """
        Args:
            exchange_name: 거래소 이름 (binance, upbit, etc.)
            api_key: API 키
            api_secret: API 시크릿
            testnet: 테스트넷 사용 여부
            options: 추가 옵션
            mode: spot(현물) | futures(선물) - binance일 때만 적용
            leverage: 레버리지 1~125 - futures 모드일 때만, binance만
        """
        self.exchange_name = exchange_name.lower()
        self.testnet = testnet
        self.options = options or {}
        self.mode = (mode or "spot").lower()
        self.leverage = max(1, min(125, leverage or 1))
        
        # binance + futures가 아닌 경우 spot만
        self.is_futures = (
            self.exchange_name == "binance"
            and self.mode == "futures"
        )
        
        # ccxt 거래소 초기화
        self.exchange = self._init_exchange(api_key, api_secret)
        
        # Rate limit tracking
        self._last_request_time: Dict[str, datetime] = {}
        
        mode_str = "futures" if self.is_futures else "spot"
        logger.info(f"ExchangeAdapter initialized: {exchange_name} ({mode_str}, leverage={self.leverage})")
    
    def _init_exchange(
        self,
        api_key: Optional[str],
        api_secret: Optional[str]
    ) -> ccxt.Exchange:
        """거래소 초기화 (binance+futures 시 binanceusdm 사용)"""
        if self.is_futures:
            exchange_class = getattr(ccxt, "binanceusdm", None)
            if exchange_class is None:
                raise ValueError("ccxt binanceusdm not found. Install: pip install ccxt")
        else:
            exchange_class = getattr(ccxt, self.exchange_name)
        
        params = {
            "rateLimit": True,
            "options": self.options.get("ccxt_options", {})
        }
        
        # 테스트넷 설정
        if self.testnet:
            if self.exchange_name in ("binance", "bybit"):
                params["testnet"] = True
        
        exchange = exchange_class(params)
        
        # API 키 설정
        if api_key:
            exchange.apiKey = api_key
            exchange.secret = api_secret
        
        return exchange
    
    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """레버리지 설정 (binance 선물 전용)"""
        if not self.is_futures:
            return True
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.exchange.set_leverage(leverage, symbol)
            )
            logger.info(f"Leverage set: {symbol} {leverage}x")
            return True
        except Exception as e:
            logger.error(f"Failed to set leverage for {symbol}: {e}")
            return False
    
    async def fetch_balance(self) -> Dict[str, Balance]:
        """잔고 조회"""
        try:
            loop = asyncio.get_event_loop()
            balance_data = await loop.run_in_executor(
                None,
                self.exchange.fetch_balance
            )
            
            balances = {}
            for currency, data in balance_data.get("total", {}).items():
                if data > 0:
                    balances[currency] = Balance(
                        total=data,
                        free=balance_data.get("free", {}).get(currency, 0),
                        used=balance_data.get("used", {}).get(currency, 0),
                        currency=currency
                    )
            
            return balances
            
        except Exception as e:
            logger.error(f"Failed to fetch balance: {e}")
            return {}
    
    async def fetch_ticker(self, symbol: str) -> Dict:
        """티커 조회"""
        try:
            loop = asyncio.get_event_loop()
            ticker = await loop.run_in_executor(
                None,
                lambda: self.exchange.fetch_ticker(symbol)
            )
            
            return {
                "symbol": symbol,
                "price": ticker.get("last", 0),
                "bid": ticker.get("bid", 0),
                "ask": ticker.get("ask", 0),
                "high": ticker.get("high", 0),
                "low": ticker.get("low", 0),
                "volume": ticker.get("baseVolume", 0),
                "timestamp": ticker.get("timestamp", 0)
            }
            
        except Exception as e:
            logger.error(f"Failed to fetch ticker: {e}")
            return {}
    
    async def create_order(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: float,
        price: Optional[float] = None,
        params: Optional[Dict] = None
    ) -> Order:
        """
        주문 생성
        
        Args:
            symbol: 심볼
            order_type: market, limit
            side: buy, sell
            amount: 수량
            price: 가격 (지정가인 경우)
            params: 추가 파라미터
        
        Returns:
            Order: 주문 정보
        """
        try:
            loop = asyncio.get_event_loop()
            
            order_params = params or {}
            if order_type == "limit" and price:
                order_params["price"] = price
            
            order_data = await loop.run_in_executor(
                None,
                lambda: self.exchange.create_order(
                    symbol, order_type, side, amount, price, order_params
                )
            )
            
            return self._parse_order(order_data)
            
        except ccxt.InsufficientFunds:
            logger.error(f"Insufficient funds for {symbol}")
            raise
        except ccxt.InvalidOrder:
            logger.error(f"Invalid order: {symbol}")
            raise
        except Exception as e:
            logger.error(f"Failed to create order: {e}")
            raise
    
    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """주문 취소"""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.exchange.cancel_order(order_id, symbol)
            )
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False
    
    async def fetch_order(self, order_id: str, symbol: str) -> Order:
        """주문 조회"""
        try:
            loop = asyncio.get_event_loop()
            order_data = await loop.run_in_executor(
                None,
                lambda: self.exchange.fetch_order(order_id, symbol)
            )
            return self._parse_order(order_data)
        except Exception as e:
            logger.error(f"Failed to fetch order {order_id}: {e}")
            raise
    
    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """미체결 주문 조회"""
        try:
            loop = asyncio.get_event_loop()
            orders_data = await loop.run_in_executor(
                None,
                lambda: self.exchange.fetch_open_orders(symbol)
            )
            
            return [self._parse_order(o) for o in orders_data]
        except Exception as e:
            logger.error(f"Failed to fetch open orders: {e}")
            return []
    
    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: int = 100
    ) -> List[Dict]:
        """OHLCV 데이터 조회"""
        try:
            loop = asyncio.get_event_loop()
            ohlcv_data = await loop.run_in_executor(
                None,
                lambda: self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            )
            
            return [
                {
                    "timestamp": datetime.fromtimestamp(d[0] / 1000),
                    "open": d[1],
                    "high": d[2],
                    "low": d[3],
                    "close": d[4],
                    "volume": d[5]
                }
                for d in ohlcv_data
            ]
        except Exception as e:
            logger.error(f"Failed to fetch OHLCV: {e}")
            return []
    
    async def fetch_trades(
        self,
        symbol: str,
        limit: int = 100
    ) -> List[Dict]:
        """체결 내역 조회"""
        try:
            loop = asyncio.get_event_loop()
            trades_data = await loop.run_in_executor(
                None,
                lambda: self.exchange.fetch_trades(symbol, limit=limit)
            )
            
            return [
                {
                    "id": t.get("id", ""),
                    "symbol": t.get("symbol", ""),
                    "price": t.get("price", 0),
                    "amount": t.get("amount", 0),
                    "side": t.get("side", ""),
                    "timestamp": datetime.fromtimestamp(t.get("timestamp", 0) / 1000),
                    "is_maker": t.get("maker", False)
                }
                for t in trades_data
            ]
        except Exception as e:
            logger.error(f"Failed to fetch trades: {e}")
            return []
    
    def _parse_order(self, order_data: Dict) -> Order:
        """주문 데이터 파싱"""
        return Order(
            id=str(order_data.get("id", "")),
            symbol=order_data.get("symbol", ""),
            type=order_data.get("type", "market"),
            side=order_data.get("side", ""),
            price=order_data.get("price", 0) or order_data.get("average", 0),
            amount=order_data.get("amount", 0),
            filled=order_data.get("filled", 0),
            remaining=order_data.get("remaining", 0),
            status=self._normalize_order_status(order_data.get("status", "")),
            timestamp=datetime.fromtimestamp(
                order_data.get("timestamp", 0) / 1000
            ) if order_data.get("timestamp") else datetime.now(),
            fee=order_data.get("fee", {}).get("cost", 0) if order_data.get("fee") else 0,
            fee_currency=order_data.get("fee", {}).get("currency", "") if order_data.get("fee") else ""
        )
    
    def _normalize_order_status(self, status: str) -> str:
        """주문 상태 정규화"""
        status_map = {
            "open": "open",
            "closed": "filled",
            "canceled": "cancelled",
            "cancelled": "cancelled",
            "expired": "expired"
        }
        return status_map.get(status.lower(), status)
    
    async def place_market_buy(
        self,
        symbol: str,
        amount: float,
        params: Optional[Dict] = None
    ) -> Order:
        """시장가 매수"""
        return await self.create_order(symbol, "market", "buy", amount, None, params)
    
    async def place_market_sell(
        self,
        symbol: str,
        amount: float,
        params: Optional[Dict] = None
    ) -> Order:
        """시장가 매도"""
        return await self.create_order(symbol, "market", "sell", amount, None, params)
    
    async def place_limit_buy(
        self,
        symbol: str,
        amount: float,
        price: float,
        params: Optional[Dict] = None
    ) -> Order:
        """지정가 매수"""
        return await self.create_order(symbol, "limit", "buy", amount, price, params)
    
    async def place_limit_sell(
        self,
        symbol: str,
        amount: float,
        price: float,
        params: Optional[Dict] = None
    ) -> Order:
        """지정가 매도"""
        return await self.create_order(symbol, "limit", "sell", amount, price, params)
    
    def get_market_info(self, symbol: str) -> Dict:
        """시장 정보 조회"""
        try:
            market = self.exchange.market(symbol)
            return {
                "symbol": symbol,
                "base": market.get("base", ""),
                "quote": market.get("quote", ""),
                "precision": market.get("precision", {}),
                "limits": market.get("limits", {}),
                "tierBased": market.get("tierBased", False)
            }
        except Exception as e:
            logger.error(f"Failed to get market info: {e}")
            return {}
    
    def get_min_order_amount(self, symbol: str) -> float:
        """최소 주문 수량 조회"""
        market_info = self.get_market_info(symbol)
        limits = market_info.get("limits", {})
        amount_limits = limits.get("amount", {})
        return amount_limits.get("min", 0) if amount_limits else 0
    
    def round_amount(self, symbol: str, amount: float) -> float:
        """수량 반올림"""
        market_info = self.get_market_info(symbol)
        precision = market_info.get("precision", {})
        amount_precision = precision.get("amount", 8)
        return round(amount, amount_precision)
    
    def round_price(self, symbol: str, price: float) -> float:
        """가격 반올림"""
        market_info = self.get_market_info(symbol)
        precision = market_info.get("precision", {})
        price_precision = precision.get("price", 8)
        return round(price, price_precision)
    
    async def close_all_positions(self, symbol: str) -> List[Order]:
        """모든 포지션 종료"""
        orders = []
        
        try:
            # 잔고 조회
            balances = await self.fetch_balance()
            
            # 해당 심볼 관련 잔고가 있으면 전량 매도
            market_info = self.get_market_info(symbol)
            base = market_info.get("base", symbol.split("/")[0])
            
            if base in balances and balances[base].free > 0:
                amount = self.round_amount(symbol, balances[base].free)
                if amount > 0:
                    order = await self.place_market_sell(symbol, amount)
                    orders.append(order)
                    logger.info(f"Closed position: sold {amount} {symbol}")
                    
        except Exception as e:
            logger.error(f"Failed to close positions: {e}")
        
        return orders
    
    async def sync_with_exchange(self) -> Dict:
        """거래소와 상태 동기화"""
        logger.info("Syncing with exchange...")
        
        sync_data = {
            "balance": {},
            "open_orders": [],
            "positions": {}
        }
        
        try:
            # 잔고 동기화
            sync_data["balance"] = await self.fetch_balance()
            
            # 미체결 주문 동기화
            sync_data["open_orders"] = await self.fetch_open_orders()
            
            logger.info(
                f"Synced: Balance={len(sync_data['balance'])} assets, "
                f"Open orders={len(sync_data['open_orders'])}"
            )
            
        except Exception as e:
            logger.error(f"Sync failed: {e}")
        
        return sync_data
