"""
기술적 지표 계산 모듈
RSI, MACD, 볼린저밴드, 이동평균 등
"""
import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime

from ..monitoring.logger import get_logger


logger = get_logger("indicators")


@dataclass
class IndicatorResult:
    """지표 계산 결과"""
    name: str
    value: float
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict = field(default_factory=dict)


@dataclass
class RSIResult:
    """RSI 결과"""
    rsi: float
    overbought: bool = False
    oversold: bool = False
    divergence: Optional[str] = None  # bullish, bearish, None
    
    @property
    def signal(self) -> str:
        if self.oversold:
            return "BUY"
        elif self.overbought:
            return "SELL"
        return "HOLD"


@dataclass
class MACDResult:
    """MACD 결과"""
    macd: float
    signal: float  # 시그널 라인 (9기간 EMA)
    histogram: float
    
    @property
    def bullish(self) -> bool:
        return self.histogram > 0
    
    @property
    def trading_signal(self) -> str:
        """매매 신호 (BUY/SELL/HOLD)"""
        if self.histogram > 0 and self.macd > self.signal:
            return "BUY"
        elif self.histogram < 0 and self.macd < self.signal:
            return "SELL"
        return "HOLD"


@dataclass
class BollingerBandsResult:
    """볼린저밴드 결과"""
    upper: float
    middle: float
    lower: float
    bandwidth: float
    position: float  # %b (0~1 사이)
    
    @property
    def signal(self) -> str:
        if self.position <= 0:
            return "BUY"  # 과매도 구간
        elif self.position >= 1:
            return "SELL"  # 과매수 구간
        return "HOLD"


@dataclass
class MovingAverageResult:
    """이동평균 결과"""
    ma: float
    ema: Optional[float] = None
    crossover: Optional[str] = None  # golden_cross, dead_cross, None
    
    @property
    def signal(self) -> str:
        if self.crossover == "golden_cross":
            return "BUY"
        elif self.crossover == "dead_cross":
            return "SELL"
        return "HOLD"


class TechnicalIndicators:
    """
    기술적 지표 계산기
    """
    
    def __init__(self):
        """초기화"""
        pass
    
    # =========================================================================
    # RSI (Relative Strength Index)
    # =========================================================================
    
    @staticmethod
    def calculate_rsi(
        prices: List[float],
        period: int = 14
    ) -> RSIResult:
        """
        RSI 계산
        
        Args:
            prices: 가격 리스트
            period: RSI 기간
        
        Returns:
            RSIResult: RSI 결과
        """
        if len(prices) < period + 1:
            logger.warning(f"Not enough data for RSI (need {period + 1}, got {len(prices)})")
            return RSIResult(rsi=50.0)
        
        # 가격 변화
        deltas = np.diff(prices)
        
        # 상승/하락 분리
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        # 평균 계산 (Wilder's smoothing)
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])
        
        # EMA 방식
        for i in range(-period, 0):
            avg_gain = (avg_gain * (period - 1) + max(gains[i], 0)) / period
            avg_loss = (avg_loss * (period - 1) + max(losses[i], 0)) / period
        
        # RSI 계산
        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        
        return RSIResult(
            rsi=rsi,
            overbought=rsi > 70,
            oversold=rsi < 30
        )
    
    @staticmethod
    def calculate_rsi_array(
        prices: List[float],
        period: int = 14
    ) -> List[float]:
        """
        RSI 배열 계산 (전체 기간)
        
        Args:
            prices: 가격 리스트
            period: RSI 기간
        
        Returns:
            RSI 값 리스트
        """
        if len(prices) < period + 1:
            return []
        
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gains = np.zeros(len(prices))
        avg_losses = np.zeros(len(prices))
        
        # 초기 평균
        avg_gains[period] = np.mean(gains[:period])
        avg_losses[period] = np.mean(losses[:period])
        
        # EMA 방식 계산
        for i in range(period + 1, len(prices)):
            avg_gains[i] = (avg_gains[i-1] * (period - 1) + gains[i-1]) / period
            avg_losses[i] = (avg_losses[i-1] * (period - 1) + losses[i-1]) / period
        
        # RSI 계산
        rs = np.divide(avg_gains, avg_losses, where=avg_losses != 0, out=np.zeros_like(avg_gains))
        rsi = 100 - (100 / (1 + rs))
        
        return rsi[period:].tolist()
    
    # =========================================================================
    # MACD (Moving Average Convergence Divergence)
    # =========================================================================
    
    @staticmethod
    def calculate_macd(
        prices: List[float],
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9
    ) -> MACDResult:
        """
        MACD 계산
        
        Args:
            prices: 가격 리스트
            fast_period: 빠른 EMA 기간
            slow_period: 느린 EMA 기간
            signal_period: 시그널 기간
        
        Returns:
            MACDResult: MACD 결과
        """
        if len(prices) < slow_period + signal_period:
            logger.warning(f"Not enough data for MACD")
            return MACDResult(macd=0, signal=0, histogram=0)
        
        prices_array = np.array(prices)
        
        # EMA 계산
        ema_fast = TechnicalIndicators._calculate_ema(prices_array, fast_period)
        ema_slow = TechnicalIndicators._calculate_ema(prices_array, slow_period)
        
        # MACD 선
        macd_line = ema_fast - ema_slow
        
        # 시그널 선
        signal_line = TechnicalIndicators._calculate_ema(macd_line, signal_period)
        
        # 히스토그램
        histogram = macd_line - signal_line
        
        # 최종값만 반환 (스칼라)
        return MACDResult(
            macd=float(macd_line[-1]),
            signal=float(signal_line[-1]),
            histogram=float(histogram[-1])
        )
    
    @staticmethod
    def _calculate_ema(prices: np.ndarray, period: int) -> np.ndarray:
        """EMA 계산 (내부 함수)"""
        ema = np.zeros_like(prices, dtype=float)
        ema[0] = prices[0]
        
        multiplier = 2 / (period + 1)
        
        for i in range(1, len(prices)):
            ema[i] = (prices[i] * multiplier) + (ema[i-1] * (1 - multiplier))
        
        return ema
    
    # =========================================================================
    # Bollinger Bands
    # =========================================================================
    
    @staticmethod
    def calculate_bollinger_bands(
        prices: List[float],
        period: int = 20,
        std_dev: float = 2.0
    ) -> BollingerBandsResult:
        """
        볼린저밴드 계산
        
        Args:
            prices: 가격 리스트
            period: 이동평균 기간
            std_dev: 표준편차 배수
        
        Returns:
            BollingerBandsResult: 볼린저밴드 결과
        """
        if len(prices) < period:
            logger.warning(f"Not enough data for Bollinger Bands")
            return BollingerBandsResult(
                upper=0, middle=0, lower=0, bandwidth=0, position=0.5
            )
        
        prices_array = np.array(prices[-period:])
        
        # 중심선 (SMA)
        middle = np.mean(prices_array)
        
        # 표준편차
        std = np.std(prices_array)
        
        # 상단/하단 밴드
        upper = middle + (std * std_dev)
        lower = middle - (std * std_dev)
        
        # Bandwidth
        bandwidth = (upper - lower) / middle if middle != 0 else 0
        
        # %B (현재价格在 밴드 내 위치)
        current_price = prices[-1]
        if upper != lower:
            position = (current_price - lower) / (upper - lower)
        else:
            position = 0.5
        
        return BollingerBandsResult(
            upper=upper,
            middle=middle,
            lower=lower,
            bandwidth=bandwidth,
            position=position
        )
    
    # =========================================================================
    # Moving Averages
    # =========================================================================
    
    @staticmethod
    def calculate_sma(prices: List[float], period: int) -> float:
        """
        SMA (Simple Moving Average) 계산
        
        Args:
            prices: 가격 리스트
            period: 기간
        
        Returns:
            SMA 값
        """
        if len(prices) < period:
            return prices[-1] if prices else 0
        return sum(prices[-period:]) / period
    
    @staticmethod
    def calculate_ema(
        prices: List[float],
        period: int
    ) -> float:
        """
        EMA (Exponential Moving Average) 계산
        
        Args:
            prices: 가격 리스트
            period: 기간
        
        Returns:
            EMA 값
        """
        if len(prices) < period:
            return prices[-1] if prices else 0
        
        prices_array = np.array(prices)
        ema_array = TechnicalIndicators._calculate_ema(prices_array, period)
        return float(ema_array[-1])
    
    @staticmethod
    def calculate_moving_averages(
        prices: List[float],
        short_period: int = 5,
        medium_period: int = 20,
        long_period: int = 60
    ) -> Dict[str, MovingAverageResult]:
        """
        다중 이동평균 계산
        
        Args:
            prices: 가격 리스트
            short_period: 단기 기간
            medium_period: 중기 기간
            long_period: 장기 기간
        
        Returns:
            이동평균 결과 딕셔너리
        """
        if len(prices) < long_period:
            logger.warning(f"Not enough data for MA (need {long_period})")
            return {}
        
        short_ma = TechnicalIndicators.calculate_sma(prices, short_period)
        medium_ma = TechnicalIndicators.calculate_sma(prices, medium_period)
        long_ma = TechnicalIndicators.calculate_sma(prices, long_period)
        
        # 골든크로스 / 데드크로스 감지
        # 이전 캔들의 MA 비교
        prev_short = TechnicalIndicators.calculate_sma(prices[:-1], short_period)
        prev_medium = TechnicalIndicators.calculate_sma(prices[:-1], medium_period)
        
        crossover = None
        if short_ma > medium_ma and prev_short <= prev_medium:
            crossover = "golden_cross"
        elif short_ma < medium_ma and prev_short >= prev_medium:
            crossover = "dead_cross"
        
        return {
            "short": MovingAverageResult(ma=short_ma, crossover=crossover),
            "medium": MovingAverageResult(ma=medium_ma, crossover=crossover),
            "long": MovingAverageResult(ma=long_ma, crossover=crossover)
        }
    
    # =========================================================================
    # 종합 지표 계산
    # =========================================================================
    
    def calculate_all(
        self,
        prices: List[float],
        rsi_period: int = 14,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        bb_period: int = 20,
        bb_std: float = 2.0
    ) -> Dict[str, any]:
        """
        모든 지표 계산
        
        Args:
            prices: 가격 리스트
            rsi_period: RSI 기간
            macd_fast: MACD 빠른 기간
            macd_slow: MACD 느린 기간
            macd_signal: MACD 시그널 기간
            bb_period: 볼린저밴드 기간
            bb_std: 볼린저밴드 표준편차
        
        Returns:
            모든 지표 결과 딕셔너리
        """
        return {
            "rsi": self.calculate_rsi(prices, rsi_period),
            "macd": self.calculate_macd(prices, macd_fast, macd_slow, macd_signal),
            "bollinger_bands": self.calculate_bollinger_bands(prices, bb_period, bb_std),
            "ma": self.calculate_moving_averages(prices)
        }
    
    # =========================================================================
    # Helper Methods
    # =========================================================================
    
    @staticmethod
    def calculate_atr(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        period: int = 14
    ) -> float:
        """
        ATR (Average True Range) 계산
        
        Args:
            highs: 고가 리스트
            lows: 저가 리스트
            closes: 종가 리스트
            period: 기간
        
        Returns:
            ATR 값
        """
        if len(highs) < period + 1:
            return 0
        
        tr = []
        for i in range(1, len(highs)):
            high_low = highs[i] - lows[i]
            high_close = abs(highs[i] - closes[i-1])
            low_close = abs(lows[i] - closes[i-1])
            tr.append(max(high_low, high_close, low_close))
        
        return sum(tr[-period:]) / period if tr else 0
    
    @staticmethod
    def calculate_stochastic(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        period: int = 14
    ) -> Tuple[float, float]:
        """
        스토캐스틱 계산
        
        Args:
            highs: 고가 리스트
            lows: 저가 리스트
            closes: 종가 리스트
            period: 기간
        
        Returns:
            (%K, %D)
        """
        if len(closes) < period:
            return 50, 50
        
        # %K 계산
        highest_high = max(highs[-period:])
        lowest_low = min(lows[-period:])
        
        if highest_high == lowest_low:
            k = 50
        else:
            k = ((closes[-1] - lowest_low) / (highest_high - lowest_low)) * 100
        
        # %D (3기간 단순 이동평균)
        return k, k  # 간소화 버전
    
    @staticmethod
    def detect_support_resistance(
        prices: List[float],
        window: int = 20
    ) -> Tuple[List[float], List[float]]:
        """
        지지/저항선 감지
        
        Args:
            prices: 가격 리스트
            window: 윈도우 크기
        
        Returns:
            (지지선 리스트, 저항선 리스트)
        """
        if len(prices) < window * 2:
            return [], []
        
        supports = []
        resistances = []
        
        for i in range(window, len(prices) - window):
            # 로컬 최소값 (지지선)
            if all(prices[i] <= prices[i-j] for j in range(1, window+1)) and \
               all(prices[i] <= prices[i+j] for j in range(1, window+1)):
                supports.append(prices[i])
            
            # 로컬 최대값 (저항선)
            if all(prices[i] >= prices[i-j] for j in range(1, window+1)) and \
               all(prices[i] >= prices[i+j] for j in range(1, window+1)):
                resistances.append(prices[i])
        
        return supports, resistances
