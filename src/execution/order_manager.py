"""
주문 관리자 모듈
주문 검증, 재시도, 중복 방지, 상태 관리
"""
import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from collections import defaultdict
import hashlib

from .exchange_adapter import ExchangeAdapter, Order
from .risk_manager import RiskManager, Position
from ..monitoring.logger import get_logger


logger = get_logger("order_manager")


@dataclass
class OrderRequest:
    """주문 요청"""
    symbol: str
    side: str  # buy, sell
    order_type: str  # market, limit
    amount: float
    price: Optional[float] = None
    signal_id: Optional[str] = None
    strategy: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    
    @property
    def id(self) -> str:
        """주문 요청 고유 ID"""
        data = f"{self.symbol}:{self.side}:{self.amount}:{self.timestamp.isoformat()}"
        return hashlib.md5(data.encode()).hexdigest()[:12]


@dataclass
class OrderResult:
    """주문 결과"""
    success: bool
    order: Optional[Order] = None
    error: Optional[str] = None
    retry_count: int = 0
    
    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "order": self.order.to_dict() if self.order else None,
            "error": self.error,
            "retry_count": self.retry_count
        }


class OrderManager:
    """
    주문 관리자
    주문 실행, 검증, 재시도, 중복 방지
    """
    
    def __init__(
        self,
        exchange_adapter: ExchangeAdapter,
        risk_manager: RiskManager,
        dry_run: bool = True,
        config: Optional[Dict[str, Any]] = None
    ):
        """
        Args:
            exchange_adapter: 거래소 어댑터
            risk_manager: 리스크 관리자
            dry_run: 시뮬레이션 모드
            config: 전체 설정 (strategies.cooldown_minutes 사용)
        """
        self.exchange = exchange_adapter
        self.risk_manager = risk_manager
        self.dry_run = dry_run
        
        # 주문 기록
        self._orders: Dict[str, Order] = {}
        self._order_history: List[Order] = []
        
        # 중복 방지 - config에서 쿨다운 분 수 읽기 (기본 1분)
        self._recent_signals: Dict[str, datetime] = {}
        cooldown_min = 1
        if config:
            cooldown_min = config.get("strategies", {}).get("cooldown_minutes", 1)
        self._signal_cooldown = timedelta(minutes=cooldown_min)
        
        # 재시도 설정
        self._max_retries = 3
        self._retry_delay = 2.0  # 초
        
        # 슬리피지 설정
        self._slippage_tolerance = 0.005  # 0.5%
        
        # 콜백
        self._on_order_update: List[callable] = []
        self._on_order_fill: List[callable] = []
        
        logger.info(f"OrderManager initialized (dry_run={dry_run})")
    
    def register_order_update_callback(self, callback: callable) -> None:
        """주문 업데이트 콜백 등록"""
        self._on_order_update.append(callback)
    
    def register_order_fill_callback(self, callback: callable) -> None:
        """주문 체결 콜백 등록"""
        self._on_order_fill.append(callback)
    
    async def execute_buy(
        self,
        symbol: str,
        amount: float,
        order_type: str = "market",
        price: Optional[float] = None,
        strategy: str = "",
        reduce_only: bool = False
    ) -> OrderResult:
        """
        매수 주문 실행
        
        Args:
            symbol: 심볼
            amount: 수량
            order_type: 주문 유형
            price: 가격 (지정가인 경우)
            strategy: 전략 이름
        
        Returns:
            OrderResult: 주문 결과
        """
        # 중복 주문 체크 (reduce_only=청산 시 스킵)
        if not reduce_only and self._is_duplicate_signal(symbol, "buy"):
            logger.warning(f"Duplicate buy signal for {symbol}, skipping")
            return OrderResult(success=False, error="Duplicate signal")
        
        # 잔고 확인 (reduce_only=선물 청산 시 스킵)
        if not reduce_only:
            balance = await self.exchange.fetch_balance()
            quote = symbol.split("/")[1]  # BTC/USDT -> USDT
            if quote not in balance or balance[quote].free <= 0:
                logger.warning(f"Insufficient balance for {quote}")
                return OrderResult(success=False, error="Insufficient balance")
            required_amount = amount * (price or await self._get_current_price(symbol))
            if required_amount > balance[quote].free:
                price_now = await self._get_current_price(symbol)
                amount = balance[quote].free / price_now
                amount = self.exchange.round_amount(symbol, amount)
        
        if amount <= 0:
            return OrderResult(success=False, error="Amount too small")
        
        # 시뮬레이션 모드
        if self.dry_run:
            logger.info(f"[DRY RUN] Buy order: {symbol} {amount} @ {price or 'market'}")
            return OrderResult(
                success=True,
                order=self._create_simulated_order(symbol, "buy", amount, price)
            )
        
        # 실제 주문 실행 (선물 숏 청산 시 reduceOnly)
        extra_params = {"reduceOnly": True} if reduce_only and getattr(self.exchange, "is_futures", False) else {}
        return await self._execute_order(symbol, "buy", amount, order_type, price, extra_params)
    
    async def execute_sell(
        self,
        symbol: str,
        amount: Optional[float] = None,
        order_type: str = "market",
        price: Optional[float] = None,
        strategy: str = ""
    ) -> OrderResult:
        """
        매도 주문 실행
        
        Args:
            symbol: 심볼
            amount: 수량 (None이면 전량)
            order_type: 주문 유형
            price: 가격
            strategy: 전략 이름
        
        Returns:
            OrderResult: 주문 결과
        """
        # 중복 주문 체크
        if self._is_duplicate_signal(symbol, "sell"):
            logger.warning(f"Duplicate sell signal for {symbol}, skipping")
            return OrderResult(success=False, error="Duplicate signal")
        
        # 수량 확인
        if amount is None:
            amount = await self._get_holdings(symbol)
        
        if amount <= 0:
            logger.warning(f"No holdings for {symbol}")
            return OrderResult(success=False, error="No holdings")
        
        # 시뮬레이션 모드
        if self.dry_run:
            logger.info(f"[DRY RUN] Sell order: {symbol} {amount} @ {price or 'market'}")
            return OrderResult(
                success=True,
                order=self._create_simulated_order(symbol, "sell", amount, price)
            )
        
        # 실제 주문 실행
        return await self._execute_order(symbol, "sell", amount, order_type, price)
    
    async def close_position(
        self,
        symbol: str,
        reason: str = ""
    ) -> OrderResult:
        """
        포지션 종료
        
        Args:
            symbol: 심볼
            reason: 종료 사유
        
        Returns:
            OrderResult: 주문 결과
        """
        position = self.risk_manager.get_position(symbol)
        if not position:
            logger.warning(f"No position found for {symbol}")
            return OrderResult(success=False, error="No position")
        
        logger.info(f"Closing position: {symbol} ({reason})")
        return await self.execute_sell(symbol, position.quantity)
    
    async def _execute_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        order_type: str,
        price: Optional[float] = None,
        params: Optional[Dict] = None
    ) -> OrderResult:
        """실제 주문 실행 (재시도 포함)"""
        retry_count = 0
        order_params = params or {}
        
        while retry_count < self._max_retries:
            try:
                # 주문 생성
                if order_type == "market":
                    if side == "buy":
                        order = await self.exchange.place_market_buy(symbol, amount, order_params, price)
                    else:
                        order = await self.exchange.place_market_sell(symbol, amount, order_params)
                else:
                    if side == "buy":
                        order = await self.exchange.place_limit_buy(symbol, amount, price, order_params)
                    else:
                        order = await self.exchange.place_limit_sell(symbol, amount, price, order_params)
                
                # 주문 기록
                self._orders[order.id] = order
                self._order_history.append(order)
                
                logger.info(f"Order placed: {order.id} {order.side} {order.symbol} {order.amount}")
                
                # 주문 업데이트 콜백
                await self._notify_order_update(order)
                
                # 시장가 주문이면 체결 대기
                if order_type == "market":
                    order = await self._wait_for_fill(order)
                    
                    if order and order.is_filled:
                        await self._notify_order_fill(order)
                
                return OrderResult(success=True, order=order)
                
            except Exception as e:
                retry_count += 1
                logger.error(f"Order failed (attempt {retry_count}): {e}")
                
                if retry_count >= self._max_retries:
                    return OrderResult(
                        success=False,
                        error=str(e),
                        retry_count=retry_count
                    )
                
                await asyncio.sleep(self._retry_delay * retry_count)
        
        return OrderResult(success=False, error="Max retries exceeded")
    
    async def _wait_for_fill(self, order: Order, timeout: float = 60) -> Optional[Order]:
        """주문 체결 대기"""
        start_time = datetime.now()
        
        while (datetime.now() - start_time).total_seconds() < timeout:
            try:
                updated_order = await self.exchange.fetch_order(order.id, order.symbol)
                
                if updated_order.is_filled:
                    return updated_order
                
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"Order status check failed: {e}")
                break
        
        return order
    
    def _is_duplicate_signal(self, symbol: str, side: str) -> bool:
        """중복 신호 체크"""
        signal_key = f"{symbol}:{side}"
        now = datetime.now()
        
        if signal_key in self._recent_signals:
            last_time = self._recent_signals[signal_key]
            if now - last_time < self._signal_cooldown:
                return True
        
        self._recent_signals[signal_key] = now
        
        # 오래된 기록 정리
        cutoff = now - self._signal_cooldown * 2
        self._recent_signals = {
            k: v for k, v in self._recent_signals.items()
            if v > cutoff
        }
        
        return False
    
    async def _get_current_price(self, symbol: str) -> float:
        """현재가 조회"""
        ticker = await self.exchange.fetch_ticker(symbol)
        return ticker.get("price", 0)
    
    async def _get_holdings(self, symbol: str) -> float:
        """보유 수량 조회"""
        market_info = self.exchange.get_market_info(symbol)
        base = market_info.get("base", symbol.split("/")[0])
        
        balances = await self.exchange.fetch_balance()
        if base in balances:
            return balances[base].free
        return 0
    
    def _create_simulated_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: Optional[float]
    ) -> Order:
        """시뮬레이션 주문 생성"""
        return Order(
            id=f"sim_{datetime.now().timestamp()}",
            symbol=symbol,
            type="market" if price is None else "limit",
            side=side,
            price=price or 0,
            amount=amount,
            filled=amount,
            remaining=0,
            status="filled",
            timestamp=datetime.now()
        )
    
    async def _notify_order_update(self, order: Order) -> None:
        """주문 업데이트 콜백 실행"""
        for callback in self._on_order_update:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(order)
                else:
                    callback(order)
            except Exception as e:
                logger.error(f"Order update callback error: {e}")
    
    async def _notify_order_fill(self, order: Order) -> None:
        """주문 체결 콜백 실행"""
        for callback in self._on_order_fill:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(order)
                else:
                    callback(order)
            except Exception as e:
                logger.error(f"Order fill callback error: {e}")
    
    async def sync_orders(self) -> None:
        """주문 상태 동기화"""
        logger.info("Syncing orders with exchange...")
        
        try:
            open_orders = await self.exchange.fetch_open_orders()
            
            # 열린 주문만 유지
            self._orders = {o.id: o for o in open_orders}
            
            logger.info(f"Synced {len(self._orders)} open orders")
            
        except Exception as e:
            logger.error(f"Order sync failed: {e}")
    
    def get_order(self, order_id: str) -> Optional[Order]:
        """주문 조회"""
        return self._orders.get(order_id)
    
    def get_orders_history(self, limit: int = 100) -> List[Order]:
        """주문 이력 조회"""
        return self._order_history[-limit:]
    
    def get_stats(self) -> Dict:
        """통계 반환"""
        total_orders = len(self._order_history)
        filled_orders = [o for o in self._order_history if o.is_filled]
        
        return {
            "total_orders": total_orders,
            "filled_orders": len(filled_orders),
            "open_orders": len(self._orders),
            "recent_signals": len(self._recent_signals),
            "dry_run": self.dry_run
        }
    
    async def cancel_all_orders(self, symbol: Optional[str] = None) -> int:
        """모든 주문 취소"""
        cancelled = 0
        
        for order_id, order in list(self._orders.items()):
            if symbol is None or order.symbol == symbol:
                if await self.exchange.cancel_order(order_id, order.symbol):
                    cancelled += 1
        
        if cancelled > 0:
            await self.sync_orders()
        
        logger.info(f"Cancelled {cancelled} orders")
        return cancelled
