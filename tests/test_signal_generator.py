"""
SignalGenerator 테스트
"""
import pytest
import sys
from pathlib import Path
from datetime import datetime

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from analysis.signal_generator import (
    SignalGenerator, RSIStrategy, MACDStrategy, BollingerBandStrategy,
    TradingSignal, SignalType
)


class TestRSIStrategy:
    """RSI 전략 테스트"""
    
    def test_rsi_buy_signal(self):
        """RSI 매수 신호 테스트"""
        strategy = RSIStrategy({"period": 14, "oversold": 30, "overbought": 70})
        
        # RSI 과매도 구간 데이터
        prices = [100] + list(range(99, 20, -1))
        signal = strategy.generate_signal("BTC/USDT", prices)
        
        assert signal.signal_type == SignalType.BUY
        assert signal.strength > 0
        assert "RSI" in signal.reason
    
    def test_rsi_sell_signal(self):
        """RSI 매도 신호 테스트"""
        strategy = RSIStrategy({"period": 14, "oversold": 30, "overbought": 70})
        
        # RSI 과매수 구간 데이터
        prices = [20] + list(range(21, 101))
        signal = strategy.generate_signal("BTC/USDT", prices)
        
        assert signal.signal_type == SignalType.SELL
        assert signal.strength > 0
        assert "RSI" in signal.reason
    
    def test_rsi_hold_signal(self):
        """RSI 홀드 신호 테스트"""
        strategy = RSIStrategy({"period": 14, "oversold": 30, "overbought": 70})
        
        # RSI 중립 구간 데이터
        prices = [50] * 20
        signal = strategy.generate_signal("BTC/USDT", prices)
        
        assert signal.signal_type == SignalType.HOLD
        assert signal.strength == 0


class TestMACDStrategy:
    """MACD 전략 테스트"""
    
    def test_macd_bullish_crossover(self):
        """MACD 골든크로스 테스트"""
        strategy = MACDStrategy({"fast_period": 12, "slow_period": 26, "signal_period": 9})
        
        # 상승 전환 데이터
        prices = [100] * 30 + list(range(101, 151))
        signal = strategy.generate_signal("ETH/USDT", prices)
        
        # 신호가 생성되어야 함 (BUY or HOLD)
        assert signal.signal_type in [SignalType.BUY, SignalType.HOLD]
    
    def test_macd_bearish_crossover(self):
        """MACD 데드크로스 테스트"""
        strategy = MACDStrategy({"fast_period": 12, "slow_period": 26, "signal_period": 9})
        
        # 하락 전환 데이터
        prices = [150] * 30 + list(range(149, 99, -1))
        signal = strategy.generate_signal("ETH/USDT", prices)
        
        # 신호가 생성되어야 함 (SELL or HOLD)
        assert signal.signal_type in [SignalType.SELL, SignalType.HOLD]


class TestBollingerBandStrategy:
    """볼린저밴드 전략 테스트"""
    
    def test_bb_buy_signal(self):
        """볼린저밴드 매수 신호 테스트"""
        strategy = BollingerBandStrategy({
            "period": 20,
            "std_dev": 2.0,
            "buy_threshold": 0.1,
            "sell_threshold": 0.9
        })
        
        # 하단 밴드 근처 데이터
        prices = [100] * 25 + [50] * 5
        signal = strategy.generate_signal("BTC/USDT", prices)
        
        assert signal.signal_type == SignalType.BUY
        assert signal.strength > 0
    
    def test_bb_sell_signal(self):
        """볼린저밴드 매도 신호 테스트"""
        strategy = BollingerBandStrategy({
            "period": 20,
            "std_dev": 2.0,
            "buy_threshold": 0.1,
            "sell_threshold": 0.9
        })
        
        # 상단 밴드 근처 데이터
        prices = [100] * 25 + [150] * 5
        signal = strategy.generate_signal("BTC/USDT", prices)
        
        assert signal.signal_type == SignalType.SELL
        assert signal.strength > 0


class TestSignalGenerator:
    """신호 생성기 테스트"""
    
    def test_create_rsi_strategy(self):
        """RSI 전략 생성 테스트"""
        generator = SignalGenerator("rsi")
        assert generator.strategy.name == "RSI"
    
    def test_create_macd_strategy(self):
        """MACD 전략 생성 테스트"""
        generator = SignalGenerator("macd")
        assert generator.strategy.name == "MACD"
    
    def test_create_bollinger_strategy(self):
        """볼린저밴드 전략 생성 테스트"""
        generator = SignalGenerator("bollinger")
        assert generator.strategy.name == "BollingerBand"
    
    def test_combined_strategy(self):
        """복합 전략 테스트"""
        generator = SignalGenerator("combined")
        assert generator.strategy.name == "Combined"
    
    def test_signal_to_dict(self):
        """신호 딕셔너리 변환 테스트"""
        signal = TradingSignal(
            symbol="BTC/USDT",
            signal_type=SignalType.BUY,
            strength=0.8,
            price=50000.0,
            reason="Test signal"
        )
        
        data = signal.to_dict()
        
        assert data["symbol"] == "BTC/USDT"
        assert data["signal_type"] == "buy"
        assert data["strength"] == 0.8
        assert data["price"] == 50000.0
        assert data["reason"] == "Test signal"
    
    def test_signal_properties(self):
        """신호 속성 테스트"""
        signal = TradingSignal(
            symbol="BTC/USDT",
            signal_type=SignalType.BUY,
            strength=0.8
        )
        
        assert signal.is_actionable == True
        assert signal.is_buy == True
        assert signal.is_sell == False
        
        sell_signal = TradingSignal(
            symbol="BTC/USDT",
            signal_type=SignalType.SELL,
            strength=0.8
        )
        
        assert sell_signal.is_actionable == True
        assert sell_signal.is_buy == False
        assert sell_signal.is_sell == True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
