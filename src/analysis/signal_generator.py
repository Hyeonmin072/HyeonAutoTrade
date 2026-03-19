"""
신호 생성기 모듈
기술적 지표 기반 + AI(Gemini) 매매 신호 생성
"""
import asyncio
from typing import Dict, List, Optional, Any
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from abc import ABC, abstractmethod

from .indicators import TechnicalIndicators, RSIResult, MACDResult, BollingerBandsResult
from ..monitoring.logger import get_logger


logger = get_logger("signal_generator")


class SignalType(Enum):
    """신호 타입"""
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    CLOSE_LONG = "close_long"
    CLOSE_SHORT = "close_short"


@dataclass
class TradingSignal:
    """거래 신호"""
    symbol: str
    signal_type: SignalType
    strength: float  # 0.0 ~ 1.0
    timestamp: datetime = field(default_factory=datetime.now)
    reason: str = ""
    indicators: Dict = field(default_factory=dict)
    price: float = 0.0
    metadata: Dict = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "signal_type": self.signal_type.value,
            "strength": self.strength,
            "timestamp": self.timestamp.isoformat(),
            "reason": self.reason,
            "indicators": self.indicators,
            "price": self.price,
            "metadata": self.metadata
        }
    
    @property
    def is_actionable(self) -> bool:
        """실행 가능한 신호인지"""
        return self.signal_type in [SignalType.BUY, SignalType.SELL]
    
    @property
    def is_buy(self) -> bool:
        return self.signal_type == SignalType.BUY
    
    @property
    def is_sell(self) -> bool:
        return self.signal_type == SignalType.SELL


class BaseStrategy(ABC):
    """
    거래 전략 기본 클래스
    """
    
    def __init__(self, name: str, config: Optional[Dict] = None):
        """
        Args:
            name: 전략 이름
            config: 전략 설정
        """
        self.name = name
        self.config = config or {}
        self.indicators = TechnicalIndicators()
    
    @abstractmethod
    def generate_signal(
        self,
        symbol: str,
        prices: List[float],
        indicators_data: Optional[Dict] = None
    ) -> TradingSignal:
        """
        신호 생성
        
        Args:
            symbol: 심볼
            prices: 가격 리스트
            indicators_data: 미리 계산된 지표 데이터
        
        Returns:
            TradingSignal: 거래 신호
        """
        pass
    
    @abstractmethod
    def get_required_data_points(self) -> int:
        """필요한 최소 데이터 포인트 수"""
        pass
    
    def validate_data(self, prices: List[float]) -> bool:
        """데이터 유효성 검증"""
        if len(prices) < self.get_required_data_points():
            logger.warning(
                f"{self.name}: Not enough data points "
                f"(need {self.get_required_data_points()}, got {len(prices)})"
            )
            return False
        return True


class RSIStrategy(BaseStrategy):
    """
    RSI 기반 전략
    - RSI < 30: 과매도 → 매수 신호
    - RSI > 70: 과매수 → 매도 신호
    """
    
    def __init__(self, config: Optional[Dict] = None):
        super().__init__("RSI", config)
        self.period = self.config.get("period", 14)
        self.overbought = self.config.get("overbought", 70)
        self.oversold = self.config.get("oversold", 30)
        self.exit_overbought = self.config.get("exit_overbought", 60)
        self.exit_oversold = self.config.get("exit_oversold", 40)
    
    def generate_signal(
        self,
        symbol: str,
        prices: List[float],
        indicators_data: Optional[Dict] = None
    ) -> TradingSignal:
        """RSI 기반 신호 생성"""
        if not self.validate_data(prices):
            return self._create_hold_signal(symbol, prices[-1], "Insufficient data")
        
        # RSI 계산
        if indicators_data and "rsi" in indicators_data:
            rsi_result = indicators_data["rsi"]
        else:
            rsi_result = self.indicators.calculate_rsi(prices, self.period)
        
        current_price = prices[-1]
        
        # 신호 판단
        if rsi_result.rsi < self.oversold:
            strength = 1.0 - (rsi_result.rsi / self.oversold)
            return TradingSignal(
                symbol=symbol,
                signal_type=SignalType.BUY,
                strength=min(strength, 1.0),
                reason=f"RSI oversold ({rsi_result.rsi:.2f})",
                price=current_price,
                indicators={"rsi": rsi_result.rsi}
            )
        
        elif rsi_result.rsi > self.overbought:
            strength = (rsi_result.rsi - self.overbought) / (100 - self.overbought)
            return TradingSignal(
                symbol=symbol,
                signal_type=SignalType.SELL,
                strength=min(strength, 1.0),
                reason=f"RSI overbought ({rsi_result.rsi:.2f})",
                price=current_price,
                indicators={"rsi": rsi_result.rsi}
            )
        
        return self._create_hold_signal(symbol, current_price, f"RSI neutral ({rsi_result.rsi:.2f})")
    
    def get_required_data_points(self) -> int:
        return self.period + 1
    
    def _create_hold_signal(
        self,
        symbol: str,
        price: float,
        reason: str
    ) -> TradingSignal:
        return TradingSignal(
            symbol=symbol,
            signal_type=SignalType.HOLD,
            strength=0.0,
            reason=reason,
            price=price
        )


class MACDStrategy(BaseStrategy):
    """
    MACD 기반 전략
    - 골든크로스 (MACD > Signal): 매수 신호
    - 데드크로스 (MACD < Signal): 매도 신호
    """
    
    def __init__(self, config: Optional[Dict] = None):
        super().__init__("MACD", config)
        self.fast_period = self.config.get("fast_period", 12)
        self.slow_period = self.config.get("slow_period", 26)
        self.signal_period = self.config.get("signal_period", 9)
    
    def generate_signal(
        self,
        symbol: str,
        prices: List[float],
        indicators_data: Optional[Dict] = None
    ) -> TradingSignal:
        """MACD 기반 신호 생성"""
        min_required = self.slow_period + self.signal_period
        if not self.validate_data(prices):
            return self._create_hold_signal(symbol, prices[-1], "Insufficient data")
        
        # MACD 계산
        if indicators_data and "macd" in indicators_data:
            macd_result = indicators_data["macd"]
        else:
            macd_result = self.indicators.calculate_macd(
                prices,
                self.fast_period,
                self.slow_period,
                self.signal_period
            )
        
        current_price = prices[-1]
        
        # 신호 판단
        if macd_result.histogram > 0 and macd_result.macd > macd_result.signal:
            strength = min(abs(macd_result.histogram) / abs(macd_result.macd) * 5, 1.0)
            return TradingSignal(
                symbol=symbol,
                signal_type=SignalType.BUY,
                strength=strength,
                reason=f"MACD bullish crossover (histogram: {macd_result.histogram:.6f})",
                price=current_price,
                indicators={
                    "macd": macd_result.macd,
                    "signal": macd_result.signal,
                    "histogram": macd_result.histogram
                }
            )
        
        elif macd_result.histogram < 0 and macd_result.macd < macd_result.signal:
            strength = min(abs(macd_result.histogram) / abs(macd_result.macd) * 5, 1.0)
            return TradingSignal(
                symbol=symbol,
                signal_type=SignalType.SELL,
                strength=strength,
                reason=f"MACD bearish crossover (histogram: {macd_result.histogram:.6f})",
                price=current_price,
                indicators={
                    "macd": macd_result.macd,
                    "signal": macd_result.signal,
                    "histogram": macd_result.histogram
                }
            )
        
        return self._create_hold_signal(symbol, current_price, "MACD neutral")
    
    def get_required_data_points(self) -> int:
        return self.slow_period + self.signal_period
    
    def _create_hold_signal(
        self,
        symbol: str,
        price: float,
        reason: str
    ) -> TradingSignal:
        return TradingSignal(
            symbol=symbol,
            signal_type=SignalType.HOLD,
            strength=0.0,
            reason=reason,
            price=price
        )


class BollingerBandStrategy(BaseStrategy):
    """
    볼린저밴드 전략
    - 가격이 하단 밴드 근처: 매수
    - 가격이 상단 밴드 근처: 매도
    """
    
    def __init__(self, config: Optional[Dict] = None):
        super().__init__("BollingerBand", config)
        self.period = self.config.get("period", 20)
        self.std_dev = self.config.get("std_dev", 2.0)
        self.buy_threshold = self.config.get("buy_threshold", 0.1)  # 10%
        self.sell_threshold = self.config.get("sell_threshold", 0.9)  # 90%
    
    def generate_signal(
        self,
        symbol: str,
        prices: List[float],
        indicators_data: Optional[Dict] = None
    ) -> TradingSignal:
        """볼린저밴드 기반 신호 생성"""
        if not self.validate_data(prices):
            return self._create_hold_signal(symbol, prices[-1], "Insufficient data")
        
        # 볼린저밴드 계산
        if indicators_data and "bollinger_bands" in indicators_data:
            bb_result = indicators_data["bollinger_bands"]
        else:
            bb_result = self.indicators.calculate_bollinger_bands(
                prices,
                self.period,
                self.std_dev
            )
        
        current_price = prices[-1]
        position = bb_result.position
        
        # 신호 판단
        if position <= self.buy_threshold:
            strength = 1.0 - (position / self.buy_threshold)
            return TradingSignal(
                symbol=symbol,
                signal_type=SignalType.BUY,
                strength=min(strength, 1.0),
                reason=f"Price near lower band (position: {position:.2%})",
                price=current_price,
                indicators={
                    "upper": bb_result.upper,
                    "middle": bb_result.middle,
                    "lower": bb_result.lower,
                    "position": position
                }
            )
        
        elif position >= self.sell_threshold:
            strength = (position - self.sell_threshold) / (1 - self.sell_threshold)
            return TradingSignal(
                symbol=symbol,
                signal_type=SignalType.SELL,
                strength=min(strength, 1.0),
                reason=f"Price near upper band (position: {position:.2%})",
                price=current_price,
                indicators={
                    "upper": bb_result.upper,
                    "middle": bb_result.middle,
                    "lower": bb_result.lower,
                    "position": position
                }
            )
        
        return self._create_hold_signal(
            symbol,
            current_price,
            f"Price in middle band (position: {position:.2%})"
        )
    
    def get_required_data_points(self) -> int:
        return self.period
    
    def _create_hold_signal(
        self,
        symbol: str,
        price: float,
        reason: str
    ) -> TradingSignal:
        return TradingSignal(
            symbol=symbol,
            signal_type=SignalType.HOLD,
            strength=0.0,
            reason=reason,
            price=price
        )


class ScalpingStrategy(BaseStrategy):
    """
    단타 전략
    - 빠른 RSI(7) + 짧은 MACD(6,13,5) 조합
    - 좁은 과매수/과매도 구간 (25/75)
    - RSI와 MACD 동시 신호 시에만 진입
    """
    
    def __init__(self, config: Optional[Dict] = None):
        super().__init__("Scalping", config)
        scalping_cfg = (config or {}).get("scalping", config or {})
        # 빠른 지표 파라미터
        self.rsi_period = scalping_cfg.get("rsi", {}).get("period", 7)
        self.rsi_oversold = scalping_cfg.get("rsi", {}).get("oversold", 25)
        self.rsi_overbought = scalping_cfg.get("rsi", {}).get("overbought", 75)
        self.macd_fast = scalping_cfg.get("macd", {}).get("fast_period", 6)
        self.macd_slow = scalping_cfg.get("macd", {}).get("slow_period", 13)
        self.macd_signal = scalping_cfg.get("macd", {}).get("signal_period", 5)
        self.require_both = scalping_cfg.get("require_both", True)  # RSI+MACD 동시 신호 필요
    
    def generate_signal(
        self,
        symbol: str,
        prices: List[float],
        indicators_data: Optional[Dict] = None
    ) -> TradingSignal:
        """단타 신호 생성 (빠른 RSI + MACD)"""
        min_required = max(
            self.rsi_period + 1,
            self.macd_slow + self.macd_signal
        )
        if not self.validate_data(prices):
            return self._create_hold_signal(symbol, prices[-1], "Insufficient data")
        
        # 단타용 지표 직접 계산 (기본 config와 다른 파라미터 사용)
        rsi_result = self.indicators.calculate_rsi(prices, self.rsi_period)
        macd_result = self.indicators.calculate_macd(
            prices, self.macd_fast, self.macd_slow, self.macd_signal
        )
        
        current_price = prices[-1]
        
        # RSI 신호
        rsi_buy = rsi_result.rsi < self.rsi_oversold
        rsi_sell = rsi_result.rsi > self.rsi_overbought
        
        # MACD 신호
        macd_buy = macd_result.histogram > 0 and macd_result.macd > macd_result.signal
        macd_sell = macd_result.histogram < 0 and macd_result.macd < macd_result.signal
        
        if self.require_both:
            # RSI + MACD 동시 일치 시에만 신호
            if rsi_buy and macd_buy:
                strength = min(
                    (1.0 - rsi_result.rsi / self.rsi_oversold) * 0.5 +
                    min(abs(macd_result.histogram) / (abs(macd_result.macd) + 1e-8) * 2, 0.5),
                    1.0
                )
                return TradingSignal(
                    symbol=symbol,
                    signal_type=SignalType.BUY,
                    strength=strength,
                    reason=f"[Scalping] RSI oversold ({rsi_result.rsi:.1f}) + MACD bullish",
                    price=current_price,
                    indicators={
                        "rsi": rsi_result.rsi,
                        "macd_histogram": macd_result.histogram,
                    }
                )
            elif rsi_sell and macd_sell:
                strength = min(
                    (rsi_result.rsi - self.rsi_overbought) / (100 - self.rsi_overbought) * 0.5 +
                    min(abs(macd_result.histogram) / (abs(macd_result.macd) + 1e-8) * 2, 0.5),
                    1.0
                )
                return TradingSignal(
                    symbol=symbol,
                    signal_type=SignalType.SELL,
                    strength=strength,
                    reason=f"[Scalping] RSI overbought ({rsi_result.rsi:.1f}) + MACD bearish",
                    price=current_price,
                    indicators={
                        "rsi": rsi_result.rsi,
                        "macd_histogram": macd_result.histogram,
                    }
                )
        else:
            # RSI 또는 MACD 중 하나만 있어도 신호 (덜 엄격)
            if rsi_buy or macd_buy:
                if rsi_buy and macd_buy:
                    strength = 0.9
                else:
                    strength = 0.6
                return TradingSignal(
                    symbol=symbol,
                    signal_type=SignalType.BUY,
                    strength=strength,
                    reason=f"[Scalping] RSI {rsi_result.rsi:.1f} / MACD {'bullish' if macd_buy else 'neutral'}",
                    price=current_price,
                    indicators={"rsi": rsi_result.rsi, "macd_histogram": macd_result.histogram}
                )
            elif rsi_sell or macd_sell:
                if rsi_sell and macd_sell:
                    strength = 0.9
                else:
                    strength = 0.6
                return TradingSignal(
                    symbol=symbol,
                    signal_type=SignalType.SELL,
                    strength=strength,
                    reason=f"[Scalping] RSI {rsi_result.rsi:.1f} / MACD {'bearish' if macd_sell else 'neutral'}",
                    price=current_price,
                    indicators={"rsi": rsi_result.rsi, "macd_histogram": macd_result.histogram}
                )
        
        return self._create_hold_signal(
            symbol,
            current_price,
            f"[Scalping] RSI {rsi_result.rsi:.1f}, MACD neutral - no entry"
        )
    
    def get_required_data_points(self) -> int:
        return self.macd_slow + self.macd_signal
    
    def _create_hold_signal(
        self,
        symbol: str,
        price: float,
        reason: str
    ) -> TradingSignal:
        return TradingSignal(
            symbol=symbol,
            signal_type=SignalType.HOLD,
            strength=0.0,
            reason=reason,
            price=price
        )


class CombinedStrategy(BaseStrategy):
    """
    복합 전략
    여러 전략의 신호를 조합
    """
    
    def __init__(self, strategies: List[BaseStrategy], config: Optional[Dict] = None):
        super().__init__("Combined", config)
        self.strategies = strategies
        self.min_agreement = config.get("min_agreement", 2)  # 최소 동의 전략 수
        self.weight_buy_threshold = config.get("weight_buy_threshold", 0.6)
        self.weight_sell_threshold = config.get("weight_sell_threshold", 0.6)
    
    def generate_signal(
        self,
        symbol: str,
        prices: List[float],
        indicators_data: Optional[Dict] = None
    ) -> TradingSignal:
        """복합 신호 생성"""
        signals = []
        total_strength = 0.0
        
        for strategy in self.strategies:
            signal = strategy.generate_signal(symbol, prices, indicators_data)
            signals.append(signal)
            total_strength += signal.strength
        
        # 신호 집계
        buy_count = sum(1 for s in signals if s.is_buy)
        sell_count = sum(1 for s in signals if s.is_sell)
        avg_strength = total_strength / len(signals) if signals else 0
        
        # 최종 신호 결정
        if buy_count >= self.min_agreement:
            avg_rsi = sum(
                s.indicators.get("rsi", 50) for s in signals if s.indicators.get("rsi")
            ) / buy_count if buy_count > 0 else 50
            
            return TradingSignal(
                symbol=symbol,
                signal_type=SignalType.BUY,
                strength=avg_strength,
                reason=f"Multiple buy signals ({buy_count}/{len(signals)})",
                price=prices[-1],
                indicators={"strategies": [s.to_dict() for s in signals]},
                metadata={"avg_rsi": avg_rsi}
            )
        
        elif sell_count >= self.min_agreement:
            return TradingSignal(
                symbol=symbol,
                signal_type=SignalType.SELL,
                strength=avg_strength,
                reason=f"Multiple sell signals ({sell_count}/{len(signals)})",
                price=prices[-1],
                indicators={"strategies": [s.to_dict() for s in signals]}
            )
        
        return TradingSignal(
            symbol=symbol,
            signal_type=SignalType.HOLD,
            strength=0.0,
            reason=f"No agreement ({buy_count} buy, {sell_count} sell)",
            price=prices[-1],
            indicators={"strategies": [s.to_dict() for s in signals]}
        )
    
    def get_required_data_points(self) -> int:
        return max(s.get_required_data_points() for s in self.strategies)


class SignalGenerator:
    """
    신호 생성기 관리자
    규칙 기반 + AI(Gemini) 전략 지원
    """
    
    STRATEGIES = {
        "rsi": RSIStrategy,
        "macd": MACDStrategy,
        "bollinger": BollingerBandStrategy,
        "scalping": ScalpingStrategy,
    }
    
    def __init__(self, strategy_name: str = "rsi", config: Optional[Dict] = None):
        """
        Args:
            strategy_name: 전략 이름 (rsi, macd, bollinger, combined, gemini, hybrid)
            config: 전체 설정 (strategies, ai 포함)
        """
        self.strategy_name = strategy_name
        self.config = config or {}
        strategies_config = self.config.get("strategies", self.config)
        ai_config = self.config.get("ai", {})
        
        # 전략 생성
        if strategy_name in self.STRATEGIES:
            # scalping은 별도 config 사용
            strat_config = (
                strategies_config.get("scalping", strategies_config)
                if strategy_name == "scalping"
                else strategies_config
            )
            self.strategy = self.STRATEGIES[strategy_name](strat_config)
        elif strategy_name == "combined":
            strategies = [
                RSIStrategy(strategies_config.get("rsi", {})),
                MACDStrategy(strategies_config.get("macd", {})),
                BollingerBandStrategy(strategies_config.get("bollinger", {}))
            ]
            self.strategy = CombinedStrategy(strategies, strategies_config)
        elif strategy_name == "gemini":
            self._init_gemini_strategy(ai_config)
        elif strategy_name == "hybrid":
            self._init_hybrid_strategy(strategies_config, ai_config)
        else:
            logger.warning(f"Unknown strategy {strategy_name}, using RSI")
            self.strategy = RSIStrategy(strategies_config)
        
        self.indicators = TechnicalIndicators()
    
    def _init_gemini_strategy(self, ai_config: Dict) -> None:
        """Gemini AI 전략 초기화"""
        from ..ai.strategies import GeminiStrategy
        from ..ai.gemini_client import GeminiConfig
        
        gemini_config = GeminiConfig(
            model=ai_config.get("model", "gemini-1.5-flash"),
            rate_limit_per_minute=ai_config.get("rate_limit_per_minute", 15),
            max_tokens_per_request=ai_config.get("max_tokens_per_request", 1024),
            cache_ttl_seconds=ai_config.get("cache_ttl_seconds", 60)
        )
        self.strategy = GeminiStrategy(config=self.config, gemini_config=gemini_config)
    
    def _init_hybrid_strategy(self, strategies_config: Dict, ai_config: Dict) -> None:
        """하이브리드 전략 초기화 (규칙 + AI)"""
        from ..ai.strategies import HybridStrategy
        from ..ai.gemini_client import GeminiConfig
        
        # 기본 규칙 전략 (config에서 지정)
        rule_name = strategies_config.get("default", "rsi")
        rule_strategy = self.STRATEGIES.get(rule_name, RSIStrategy)(strategies_config)
        
        gemini_config = GeminiConfig(
            model=ai_config.get("model", "gemini-1.5-flash"),
            rate_limit_per_minute=ai_config.get("rate_limit_per_minute", 15),
            max_tokens_per_request=ai_config.get("max_tokens_per_request", 1024),
            cache_ttl_seconds=ai_config.get("cache_ttl_seconds", 60)
        )
        self.strategy = HybridStrategy(
            rule_strategy=rule_strategy,
            gemini_config=gemini_config,
            config=self.config
        )
    
    def generate_signal(
        self,
        symbol: str,
        prices: List[float],
        indicators_data: Optional[Dict] = None
    ) -> TradingSignal:
        """
        신호 생성 (동기) - 규칙 전략용
        """
        return self.strategy.generate_signal(symbol, prices, indicators_data)
    
    async def generate_signal_async(
        self,
        symbol: str,
        prices: List[float],
        indicators_data: Optional[Dict] = None
    ) -> TradingSignal:
        """
        신호 생성 (비동기) - AI 전략 지원
        동기/비동기 전략 모두 처리
        """
        if asyncio.iscoroutinefunction(self.strategy.generate_signal):
            return await self.strategy.generate_signal(symbol, prices, indicators_data)
        return self.strategy.generate_signal(symbol, prices, indicators_data)
    
    def calculate_indicators(
        self,
        prices: List[float],
        **kwargs
    ) -> Dict:
        """지표 계산"""
        return self.indicators.calculate_all(prices, **kwargs)
    
    @classmethod
    def create_strategy(cls, name: str, config: Optional[Dict] = None) -> BaseStrategy:
        """전략 생성 (외부용)"""
        if name in cls.STRATEGIES:
            return cls.STRATEGIES[name](config)
        raise ValueError(f"Unknown strategy: {name}")
