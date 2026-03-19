"""
Indicators 모듈 테스트
"""
import pytest
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from analysis.indicators import TechnicalIndicators


class TestRSI:
    """RSI 테스트"""
    
    def test_rsi_oversold(self):
        """RSI 과매도 구간 테스트"""
        indicators = TechnicalIndicators()
        
        # 지속적인 하락 -> RSI 낮음
        prices = [100, 95, 90, 85, 82, 80, 78, 76, 74, 72, 70, 68, 66, 64, 63]
        result = indicators.calculate_rsi(prices, period=14)
        
        assert result.rsi < 70, f"Expected RSI < 70, got {result.rsi}"
        assert result.oversold == (result.rsi < 30), "Oversold flag incorrect"
    
    def test_rsi_overbought(self):
        """RSI 과매수 구간 테스트"""
        indicators = TechnicalIndicators()
        
        # 지속적인 상승 -> RSI 높음
        prices = [60, 65, 70, 75, 78, 82, 85, 88, 91, 94, 96, 98, 100, 102, 103]
        result = indicators.calculate_rsi(prices, period=14)
        
        assert result.rsi > 30, f"Expected RSI > 30, got {result.rsi}"
        assert result.overbought == (result.rsi > 70), "Overbought flag incorrect"
    
    def test_rsi_insufficient_data(self):
        """데이터 부족 시 처리"""
        indicators = TechnicalIndicators()
        
        # 데이터가 너무 적음
        prices = [100, 95, 90]
        result = indicators.calculate_rsi(prices, period=14)
        
        # 최소 데이터가 없으면 기본값 반환
        assert result.rsi == 50.0, "Should return neutral RSI for insufficient data"
    
    def test_rsi_array(self):
        """RSI 배열 계산 테스트"""
        indicators = TechnicalIndicators()
        
        # 충분한 데이터
        prices = [100] + list(range(99, 50, -1)) + list(range(50, 100))
        rsi_array = indicators.calculate_rsi_array(prices, period=14)
        
        assert len(rsi_array) > 0, "Should return RSI values"
        assert all(0 <= rsi <= 100 for rsi in rsi_array), "RSI should be between 0 and 100"


class TestMACD:
    """MACD 테스트"""
    
    def test_macd_bullish(self):
        """MACD 골든크로스 테스트"""
        indicators = TechnicalIndicators()
        
        # 상승 추세
        prices = list(range(50, 150))
        result = indicators.calculate_macd(prices)
        
        assert isinstance(result.macd, float), "MACD should be float"
        assert isinstance(result.signal, float), "Signal should be float"
        assert isinstance(result.histogram, float), "Histogram should be float"
    
    def test_macd_insufficient_data(self):
        """데이터 부족 시 처리"""
        indicators = TechnicalIndicators()
        
        prices = [100, 105, 110]
        result = indicators.calculate_macd(prices)
        
        # 데이터 부족 시 0 반환
        assert result.macd == 0
        assert result.signal == 0
        assert result.histogram == 0


class TestBollingerBands:
    """볼린저밴드 테스트"""
    
    def test_bollinger_bands_structure(self):
        """볼린저밴드 구조 테스트"""
        indicators = TechnicalIndicators()
        
        prices = list(range(50, 150)) + list(range(150, 50, -1))
        result = indicators.calculate_bollinger_bands(prices, period=20, std_dev=2.0)
        
        # 상단 > 중심 > 하단
        assert result.upper > result.middle, "Upper band should be above middle"
        assert result.middle > result.lower, "Middle band should be above lower band"
        
        # Bandwidth는 양수
        assert result.bandwidth > 0, "Bandwidth should be positive"
        
        # Position은 0~1 사이
        assert 0 <= result.position <= 1, "Position should be between 0 and 1"
    
    def test_bollinger_bands_position(self):
        """밴드 내 위치 테스트"""
        indicators = TechnicalIndicators()
        
        # 특정 구간 가격
        prices = [100] * 20
        prices.extend([80] * 5)  # 하단 근처
        
        result = indicators.calculate_bollinger_bands(prices, period=20)
        
        # 80은 lower band 근처 -> position ≈ 0
        assert result.position < 0.3, "Price near lower band should have low position"


class TestMovingAverages:
    """이동평균 테스트"""
    
    def test_sma_calculation(self):
        """SMA 계산 테스트"""
        indicators = TechnicalIndicators()
        
        prices = [10, 20, 30, 40, 50]
        sma = indicators.calculate_sma(prices, period=3)
        
        assert sma == 40.0, f"Expected 40.0, got {sma}"
    
    def test_ema_calculation(self):
        """EMA 계산 테스트"""
        indicators = TechnicalIndicators()
        
        prices = [10, 20, 30, 40, 50]
        ema = indicators.calculate_ema(prices, period=3)
        
        # EMA는 최근 값에 더 가중치
        assert 30 < ema < 50, f"EMA should be between 30 and 50, got {ema}"
    
    def test_multiple_mas(self):
        """다중 이동평균 테스트"""
        indicators = TechnicalIndicators()
        
        prices = list(range(1, 101))  # 1~100
        result = indicators.calculate_moving_averages(prices, 5, 20, 60)
        
        assert "short" in result, "Should have short MA"
        assert "medium" in result, "Should have medium MA"
        assert "long" in result, "Should have long MA"
        
        # 단기 > 중기 > 장기 (상승 추세)
        assert result["short"].ma > result["medium"].ma
        assert result["medium"].ma > result["long"].ma


class TestSupportResistance:
    """지지/저항선 테스트"""
    
    def test_support_detection(self):
        """지지선 감지 테스트"""
        indicators = TechnicalIndicators()
        
        # V字 패턴
        prices = [100, 90, 80, 90, 100, 110, 120]
        supports, resistances = indicators.detect_support_resistance(prices, window=2)
        
        # 80 근처에 지지선 감지
        assert len(supports) > 0, "Should detect support"
    
    def test_resistance_detection(self):
        """저항선 감지 테스트"""
        indicators = TechnicalIndicators()
        
        # 逆V字 패턴
        prices = [80, 90, 100, 90, 80, 70, 60]
        supports, resistances = indicators.detect_support_resistance(prices, window=2)
        
        # 100 근처에 저항선 감지
        assert len(resistances) > 0, "Should detect resistance"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
