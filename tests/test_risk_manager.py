"""
RiskManager 테스트
"""
import pytest
import sys
from pathlib import Path
from datetime import datetime

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from execution.risk_manager import (
    RiskManager, RiskConfig, Position, RiskLevel
)


class TestPosition:
    """포지션 테스트"""
    
    def test_long_position_pnl(self):
        """_LONG 포지션 손익 계산 테스트"""
        position = Position(
            symbol="BTC/USDT",
            side="long",
            entry_price=50000.0,
            current_price=55000.0,
            quantity=1.0
        )
        
        assert position.unrealized_pnl > 0, "Long position should have positive PnL when price rises"
        assert position.unrealized_pnl_percent > 0, "Long position should have positive % when price rises"
    
    def test_short_position_pnl(self):
        """SHORT 포지션 손익 계산 테스트"""
        position = Position(
            symbol="BTC/USDT",
            side="short",
            entry_price=50000.0,
            current_price=45000.0,
            quantity=1.0
        )
        
        assert position.unrealized_pnl > 0, "Short position should have positive PnL when price falls"
        assert position.unrealized_pnl_percent > 0, "Short position should have positive % when price falls"
    
    def test_stop_loss_trigger(self):
        """손절 조건 테스트"""
        position = Position(
            symbol="BTC/USDT",
            side="long",
            entry_price=50000.0,
            current_price=47500.0,  # -5%
            quantity=1.0
        )
        
        assert position.should_stop_loss(5.0) == True, "Should trigger stop loss at -5%"
    
    def test_take_profit_trigger(self):
        """익절 조건 테스트"""
        position = Position(
            symbol="BTC/USDT",
            side="long",
            entry_price=50000.0,
            current_price=55000.0,  # +10%
            quantity=1.0
        )
        
        assert position.should_take_profit(10.0) == True, "Should trigger take profit at +10%"


class TestRiskConfig:
    """리스크 설정 테스트"""
    
    def test_default_config(self):
        """기본 설정 테스트"""
        config = RiskConfig()
        
        assert config.stop_loss_percent == -5.0
        assert config.take_profit_percent == 10.0
        assert config.max_positions == 3
        assert config.max_daily_loss_percent == -3.0
        assert config.position_size_percent == 10.0
    
    def test_custom_config(self):
        """커스텀 설정 테스트"""
        config = RiskConfig(
            stop_loss_percent=-3.0,
            take_profit_percent=5.0,
            max_positions=5
        )
        
        assert config.stop_loss_percent == -3.0
        assert config.take_profit_percent == 5.0
        assert config.max_positions == 5


class TestRiskManager:
    """리스크 관리자 테스트"""
    
    def test_add_position(self):
        """포지션 추가 테스트"""
        config = RiskConfig()
        manager = RiskManager(config)
        manager.set_balance(10000.0)
        
        position = Position(
            symbol="BTC/USDT",
            side="long",
            entry_price=50000.0,
            current_price=50000.0,
            quantity=0.1
        )
        
        result = manager.add_position(position)
        
        assert result == True, "Should successfully add position"
        assert manager.has_position("BTC/USDT"), "Position should be tracked"
    
    def test_max_positions_limit(self):
        """최대 포지션 수 제한 테스트"""
        config = RiskConfig(max_positions=2)
        manager = RiskManager(config)
        manager.set_balance(100000.0)
        
        # 2개 포지션 추가
        for i, symbol in enumerate(["BTC/USDT", "ETH/USDT"]):
            position = Position(
                symbol=symbol,
                side="long",
                entry_price=50000.0,
                current_price=50000.0,
                quantity=0.1
            )
            result = manager.add_position(position)
            assert result == True
        
        # 3번째는 실패해야 함
        position = Position(
            symbol="SOL/USDT",
            side="long",
            entry_price=100.0,
            current_price=100.0,
            quantity=1.0
        )
        result = manager.add_position(position)
        assert result == False, "Should reject 3rd position"
    
    def test_remove_position(self):
        """포지션 제거 테스트"""
        config = RiskConfig()
        manager = RiskManager(config)
        manager.set_balance(10000.0)
        
        position = Position(
            symbol="BTC/USDT",
            side="long",
            entry_price=50000.0,
            current_price=55000.0,
            quantity=0.1
        )
        
        manager.add_position(position)
        removed = manager.remove_position("BTC/USDT", 500.0)
        
        assert removed is not None, "Should return removed position"
        assert not manager.has_position("BTC/USDT"), "Position should be removed"
    
    def test_can_trade_when_blocked(self):
        """거래 차단 테스트"""
        config = RiskConfig(max_daily_loss_percent=-3.0)
        manager = RiskManager(config)
        manager.set_balance(1000.0)  # 초기 잔고
        
        # 손실 기록 (시뮬레이션)
        today = datetime.now().date()
        manager.daily_stats[today.isoformat()] = type('obj', (object,), {
            'trading_blocked': True,
            'block_reason': 'Daily loss limit reached',
            'total_pnl': -50.0
        })()
        
        assert manager.can_trade("BTC/USDT") == False, "Should not trade when blocked"
    
    def test_risk_level_safe(self):
        """안전 위험 수준 테스트"""
        config = RiskConfig()
        manager = RiskManager(config)
        manager.set_balance(10000.0)  # 초기 잔고와 동일
        manager.initial_balance = 10000.0
        
        level = manager.get_risk_level()
        assert level == RiskLevel.SAFE, "Should be SAFE when balance is stable"
    
    def test_risk_level_critical(self):
        """위험 위험 수준 테스트"""
        config = RiskConfig()
        manager = RiskManager(config)
        manager.initial_balance = 10000.0
        manager.set_balance(8000.0)  # 20% 감소
        
        level = manager.get_risk_level()
        assert level in [RiskLevel.HIGH, RiskLevel.CRITICAL], "Should be HIGH or CRITICAL when balance drops"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
