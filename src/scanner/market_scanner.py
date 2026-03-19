"""
시장 스캐너 모듈
전체 마켓 스캔 → 급등락/거래량 상위 코인 동적 선정
"""
import asyncio
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

import ccxt

from ..monitoring.logger import get_logger


logger = get_logger("market_scanner")


@dataclass
class TickerInfo:
    """스캔된 티커 정보"""
    symbol: str
    price: float
    volume_24h: float
    change_percent_24h: float
    high_24h: float
    low_24h: float
    quote: str  # KRW, USDT 등


@dataclass
class ScanResult:
    """스캔 결과"""
    symbols: List[str]
    tickers: Dict[str, TickerInfo] = field(default_factory=dict)
    scan_time: Optional[str] = None


class MarketScanner:
    """
    시장 스캐너
    거래소 전체 마켓을 스캔하여 급등락/거래량 상위 코인 선정
    """
    
    def __init__(
        self,
        exchange_name: str,
        quote: str = "KRW",
        max_symbols: int = 15,
        min_change_percent: float = 2.0,
        min_volume_quote: float = 0,
        sort_by: str = "change_abs",
        testnet: bool = False
    ):
        """
        Args:
            exchange_name: 거래소 (upbit, binance)
            quote: 기준 화폐 (KRW, USDT)
            max_symbols: 선정할 최대 심볼 수
            min_change_percent: 최소 24h 변동률 (%) - 이 이상만 후보
            min_volume_quote: 최소 24h 거래량 (quote 기준)
            sort_by: 정렬 기준 - change_abs(변동률), change_up(상승률), volume(거래량)
            testnet: 테스트넷 사용
        """
        self.exchange_name = exchange_name.lower()
        self.quote = quote.upper()
        self.max_symbols = max(5, min(50, max_symbols))
        self.min_change_percent = min_change_percent
        self.min_volume_quote = min_volume_quote
        self.sort_by = sort_by
        self.testnet = testnet
        
        self.exchange = self._init_exchange()
        self._last_result: Optional[ScanResult] = None
    
    def _init_exchange(self) -> ccxt.Exchange:
        """ccxt 거래소 초기화 (공개 API만 사용)"""
        exchange_class = getattr(ccxt, self.exchange_name)
        params = {"rateLimit": True}
        if self.testnet and self.exchange_name == "binance":
            params["testnet"] = True
        
        exchange = exchange_class(params)
        # 스캐너는 공개 API만 사용 (선택적)
        return exchange
    
    def _symbol_to_quote(self, symbol: str) -> str:
        """심볼에서 quote 추출 (BTC/KRW -> KRW)"""
        if "/" in symbol:
            return symbol.split("/")[-1]
        return ""
    
    def _filter_by_quote(self, symbols: List[str]) -> List[str]:
        """quote 기준 필터 (KRW 또는 USDT 마켓만)"""
        return [s for s in symbols if self._symbol_to_quote(s) == self.quote]
    
    def _parse_ticker(self, symbol: str, data: Dict) -> Optional[TickerInfo]:
        """ccxt 티커 데이터 파싱"""
        try:
            change_pct = float(data.get("percentage") or data.get("change", 0) or 0)
            if data.get("change") and data.get("last"):
                # percentage가 없을 때 계산
                try:
                    prev = float(data.get("last", 0)) - float(data.get("change", 0))
                    if prev and prev > 0:
                        change_pct = (float(data.get("change", 0)) / prev) * 100
                except (TypeError, ZeroDivisionError):
                    pass
            
            volume = float(data.get("baseVolume") or data.get("volume") or 0)
            # quote 기준 거래량 (업비트: 원화, 바이낸스: USDT)
            quote_volume = float(data.get("quoteVolume") or 0)
            if quote_volume <= 0 and volume and data.get("last"):
                quote_volume = volume * float(data.get("last", 0))
            
            return TickerInfo(
                symbol=symbol,
                price=float(data.get("last") or data.get("close") or 0),
                volume_24h=quote_volume or volume * (data.get("last") or 0),
                change_percent_24h=change_pct,
                high_24h=float(data.get("high") or 0),
                low_24h=float(data.get("low") or 0),
                quote=self._symbol_to_quote(symbol)
            )
        except (TypeError, ValueError, KeyError) as e:
            logger.debug(f"Parse ticker error {symbol}: {e}")
            return None
    
    async def scan(self) -> ScanResult:
        """
        시장 스캔 실행
        Returns:
            ScanResult: 선정된 심볼 목록 및 티커 정보
        """
        try:
            loop = asyncio.get_event_loop()
            
            # 1. 마켓 로드
            await loop.run_in_executor(None, self.exchange.load_markets)
            all_symbols = list(self.exchange.markets.keys())
            symbols = self._filter_by_quote(all_symbols)
            
            if not symbols:
                logger.warning(f"No {self.quote} markets found")
                return ScanResult(symbols=[], tickers={})
            
            # 2. 티커 조회 (거래소별 최적화)
            tickers_data: Dict[str, Dict] = {}
            
            if self.exchange_name == "upbit":
                # 업비트: fetch_tickers가 symbols 제한 있을 수 있음 → 배치
                batch_size = 100
                for i in range(0, len(symbols), batch_size):
                    batch = symbols[i:i + batch_size]
                    try:
                        result = await loop.run_in_executor(
                            None,
                            lambda b=batch: self.exchange.fetch_tickers(b)
                        )
                        tickers_data.update(result)
                    except Exception as e:
                        logger.warning(f"Upbit ticker batch error: {e}")
                        # 개별 fetch 폴백
                        for s in batch:
                            try:
                                t = await loop.run_in_executor(
                                    None,
                                    lambda sym=s: self.exchange.fetch_ticker(sym)
                                )
                                if t:
                                    tickers_data[s] = t
                            except Exception:
                                pass
                            await asyncio.sleep(0.2)  # rate limit
            else:
                # 바이낸스 등: fetch_tickers 전체 또는 symbols
                try:
                    result = await loop.run_in_executor(
                        None,
                        lambda: self.exchange.fetch_tickers(symbols)
                    )
                    tickers_data = result
                except Exception as e:
                    logger.warning(f"fetch_tickers error: {e}, trying fetch_ticker loop")
                    for s in symbols[:50]:  # 상위 50개만
                        try:
                            t = await loop.run_in_executor(
                                None,
                                lambda sym=s: self.exchange.fetch_ticker(sym)
                            )
                            if t:
                                tickers_data[s] = t
                        except Exception:
                            pass
                        await asyncio.sleep(0.15)
            
            # 3. 파싱 및 필터
            ticker_infos: Dict[str, TickerInfo] = {}
            for sym, data in tickers_data.items():
                if not isinstance(data, dict):
                    continue
                info = self._parse_ticker(sym, data)
                if not info:
                    continue
                if abs(info.change_percent_24h) < self.min_change_percent:
                    continue
                if info.volume_24h < self.min_volume_quote:
                    continue
                ticker_infos[sym] = info
            
            # 4. 정렬 및 상위 N개 선정
            if self.sort_by == "change_up":
                sorted_items = sorted(
                    ticker_infos.items(),
                    key=lambda x: x[1].change_percent_24h,
                    reverse=True
                )
            elif self.sort_by == "volume":
                sorted_items = sorted(
                    ticker_infos.items(),
                    key=lambda x: x[1].volume_24h,
                    reverse=True
                )
            else:
                # change_abs: 변동률 절대값 (급등+급락 모두)
                sorted_items = sorted(
                    ticker_infos.items(),
                    key=lambda x: abs(x[1].change_percent_24h),
                    reverse=True
                )
            
            selected = [s for s, _ in sorted_items[: self.max_symbols]]
            result_tickers = {s: ticker_infos[s] for s in selected if s in ticker_infos}
            
            from datetime import datetime
            self._last_result = ScanResult(
                symbols=selected,
                tickers=result_tickers,
                scan_time=datetime.now().isoformat()
            )
            
            logger.info(
                f"Market scan: {len(selected)} symbols selected "
                f"(change>={self.min_change_percent}%, sort={self.sort_by})"
            )
            return self._last_result
            
        except Exception as e:
            logger.error(f"Market scan failed: {e}", exc_info=True)
            if self._last_result:
                return self._last_result
            return ScanResult(symbols=[], tickers={})
    
    def get_last_result(self) -> Optional[ScanResult]:
        """마지막 스캔 결과 반환"""
        return self._last_result
