"""
리스크 관리 모듈
손절/익절, 포지션 한도, 일일 손실 한도 관리
"""
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from ..monitoring.logger import get_logger


logger = get_logger("risk_manager")


class RiskLevel(Enum):
    """위험 수준"""
    SAFE = "safe"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Position:
    """포지션 정보"""
    symbol: str
    side: str  # long, short
    entry_price: float
    current_price: float
    quantity: float
    entry_time: datetime = field(default_factory=datetime.now)
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    unrealized_pnl: float = 0.0
    unrealized_pnl_percent: float = 0.0
    
    def update(self, current_price: float) -> None:
        """포지션 업데이트"""
        self.current_price = current_price
        
        # 손익 계산
        if self.side == "long":
            self.unrealized_pnl = (current_price - self.entry_price) * self.quantity
            self.unrealized_pnl_percent = (
                (current_price - self.entry_price) / self.entry_price * 100
            )
        else:  # short
            self.unrealized_pnl = (self.entry_price - current_price) * self.quantity
            self.unrealized_pnl_percent = (
                (self.entry_price - current_price) / self.entry_price * 100
            )
    
    def should_stop_loss(self, threshold: float) -> bool:
        """손절 조건 확인"""
        if self.side == "long":
            return self.unrealized_pnl_percent <= -abs(threshold)
        else:
            return self.unrealized_pnl_percent <= -abs(threshold)
    
    def should_take_profit(self, threshold: float) -> bool:
        """익절 조건 확인"""
        return self.unrealized_pnl_percent >= abs(threshold)
    
    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "quantity": self.quantity,
            "entry_time": self.entry_time.isoformat(),
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "unrealized_pnl": self.unrealized_pnl,
            "unrealized_pnl_percent": self.unrealized_pnl_percent
        }


@dataclass
class RiskConfig:
    """리스크 설정"""
    stop_loss_percent: float = -5.0
    take_profit_percent: float = 10.0
    max_positions: int = 3
    max_daily_loss_percent: float = -3.0
    position_size_percent: float = 10.0
    min_balance_percent: float = 5.0
    leverage: int = 1  # 선물 전용 (1=현물)


@dataclass
class DailyStats:
    """일일 통계"""
    date: datetime
    trades_count: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    trading_blocked: bool = False
    block_reason: str = ""
    
    @property
    def win_rate(self) -> float:
        if self.trades_count == 0:
            return 0.0
        return self.winning_trades / self.trades_count * 100
    
    @property
    def daily_loss_percent(self) -> float:
        return self.total_pnl


class RiskManager:
    """
    리스크 관리자
    24시간 연속 거래를 위한 리스크 통제
    """
    
    def __init__(self, config: RiskConfig):
        """
        Args:
            config: 리스크 설정
        """
        self.config = config
        self.positions: Dict[str, Position] = {}
        self.daily_stats: Dict[str, DailyStats] = {}
        self.total_balance: float = 0.0
        self.initial_balance: float = 0.0
        
        # 일일 손실 기록
        self._daily_losses: List[float] = []
        self._consecutive_losses: int = 0
    
    def set_balance(self, balance: float) -> None:
        """잔고 설정"""
        if self.initial_balance == 0:
            self.initial_balance = balance
        self.total_balance = balance
    
    def add_position(self, position: Position) -> bool:
        """
        포지션 추가
        
        Returns:
            추가 성공 여부
        """
        # 포지션 수 제한 확인
        if len(self.positions) >= self.config.max_positions:
            logger.warning(
                f"Max positions ({self.config.max_positions}) reached"
            )
            return False
        
        # 동일 심볼 포지션 확인
        if position.symbol in self.positions:
            logger.warning(f"Position already exists for {position.symbol}")
            return False
        
        # 잔고 충분 확인 (선물: 레버리지 적용)
        position_value = position.entry_price * position.quantity
        max_position_value = self.total_balance * (self.config.position_size_percent / 100) * self.config.leverage
        
        if position_value > max_position_value:
            logger.warning(
                f"Position size exceeds limit: {position_value} > {max_position_value}"
            )
            # 수량 조정
            position.quantity = max_position_value / position.entry_price
        
        # 최소 잔고 확인 (선물: 마진만 차감)
        margin_used = position_value / self.config.leverage
        new_balance = self.total_balance - margin_used
        min_balance = self.initial_balance * (self.config.min_balance_percent / 100)
        
        if new_balance < min_balance:
            logger.warning(
                f"Would exceed min balance: {new_balance} < {min_balance}"
            )
            return False
        
        # 손절/익절 설정
        if position.side == "long":
            position.stop_loss = position.entry_price * (1 + self.config.stop_loss_percent / 100)
            position.take_profit = position.entry_price * (1 + self.config.take_profit_percent / 100)
        else:
            position.stop_loss = position.entry_price * (1 - self.config.stop_loss_percent / 100)
            position.take_profit = position.entry_price * (1 - self.config.take_profit_percent / 100)
        
        self.positions[position.symbol] = position
        logger.info(f"Position added: {position.symbol} {position.side} {position.quantity} @ {position.entry_price}")
        
        return True
    
    def remove_position(self, symbol: str, realized_pnl: float = 0.0) -> Optional[Position]:
        """포지션 제거"""
        if symbol not in self.positions:
            return None
        
        position = self.positions.pop(symbol)
        
        # 잔고 업데이트
        self.total_balance += position.unrealized_pnl + realized_pnl
        
        # 일일 통계 업데이트
        self._update_daily_stats(symbol, position, realized_pnl)
        
        logger.info(
            f"Position removed: {symbol} | "
            f"PnL: {position.unrealized_pnl + realized_pnl:.2f} | "
            f"Balance: {self.total_balance:.2f}"
        )
        
        return position
    
    def update_positions(self, prices: Dict[str, float]) -> List[str]:
        """
        모든 포지션 업데이트 및 손절/익절 체크
        
        Returns:
            종료해야 할 포지션 심볼 목록
        """
        to_close = []
        
        for symbol, position in self.positions.items():
            if symbol in prices:
                position.update(prices[symbol])
                
                # 손절 체크
                if position.should_stop_loss(abs(self.config.stop_loss_percent)):
                    logger.warning(
                        f"Stop loss triggered: {symbol} "
                        f"({position.unrealized_pnl_percent:.2f}%)"
                    )
                    to_close.append(symbol)
                
                # 익절 체크
                elif position.should_take_profit(abs(self.config.take_profit_percent)):
                    logger.info(
                        f"Take profit triggered: {symbol} "
                        f"({position.unrealized_pnl_percent:.2f}%)"
                    )
                    to_close.append(symbol)
        
        return to_close
    
    def _update_daily_stats(
        self,
        symbol: str,
        position: Position,
        realized_pnl: float
    ) -> None:
        """일일 통계 업데이트"""
        today = datetime.now().date()
        date_key = today.isoformat()
        
        if date_key not in self.daily_stats:
            self.daily_stats[date_key] = DailyStats(date=datetime.now())
        
        stats = self.daily_stats[date_key]
        stats.trades_count += 1
        stats.total_pnl += position.unrealized_pnl + realized_pnl
        
        pnl = position.unrealized_pnl + realized_pnl
        if pnl > 0:
            stats.winning_trades += 1
            stats.largest_win = max(stats.largest_win, pnl)
            self._consecutive_losses = 0
        else:
            stats.losing_trades += 1
            stats.largest_loss = min(stats.largest_loss, pnl)
            self._consecutive_losses += 1
        
        # 일일 손실 한도 체크
        daily_loss_percent = (
            stats.total_pnl / self.initial_balance * 100
            if self.initial_balance > 0 else 0
        )
        
        if daily_loss_percent <= self.config.max_daily_loss_percent:
            stats.trading_blocked = True
            stats.block_reason = f"Daily loss limit reached ({daily_loss_percent:.2f}%)"
            logger.warning(stats.block_reason)
    
    def can_trade(self, symbol: str) -> bool:
        """거래 가능 여부 확인"""
        return self.get_block_reason(symbol) is None
    
    def get_block_reason(self, symbol: str) -> Optional[str]:
        """거래 불가 사유 반환 (가능하면 None)"""
        today = datetime.now().date()
        date_key = today.isoformat()
        
        if date_key in self.daily_stats:
            stats = self.daily_stats[date_key]
            if stats.trading_blocked:
                return stats.block_reason or "일일 손실 한도 도달"
        
        if symbol not in self.positions and len(self.positions) >= self.config.max_positions:
            return f"최대 포지션 수 초과 ({len(self.positions)}/{self.config.max_positions})"
        
        min_balance = self.initial_balance * (self.config.min_balance_percent / 100)
        if self.total_balance < min_balance:
            return f"잔고 부족 (현재 {self.total_balance:.0f} < 최소 {min_balance:.0f})"
        
        return None
    
    def get_position(self, symbol: str) -> Optional[Position]:
        """포지션 조회"""
        return self.positions.get(symbol)
    
    def has_position(self, symbol: str) -> bool:
        """포지션 존재 여부"""
        return symbol in self.positions
    
    def get_risk_level(self) -> RiskLevel:
        """현재 위험 수준 반환"""
        # 포지션 비율
        position_ratio = len(self.positions) / self.config.max_positions
        
        # 잔고 비율
        balance_ratio = self.total_balance / self.initial_balance
        
        # 일일 손실
        today = datetime.now().date()
        date_key = today.isoformat()
        daily_loss = 0.0
        if date_key in self.daily_stats:
            daily_loss = self.daily_stats[date_key].daily_loss_percent
        
        # 위험 수준 판단
        if balance_ratio < 0.9 or daily_loss < -2.0:
            return RiskLevel.CRITICAL
        elif balance_ratio < 0.95 or daily_loss < -1.0:
            return RiskLevel.HIGH
        elif balance_ratio < 0.98 or position_ratio > 0.7:
            return RiskLevel.MODERATE
        else:
            return RiskLevel.SAFE
    
    def get_stats(self) -> Dict:
        """통계 반환"""
        today = datetime.now().date()
        date_key = today.isoformat()
        
        return {
            "balance": self.total_balance,
            "initial_balance": self.initial_balance,
            "balance_ratio": self.total_balance / self.initial_balance if self.initial_balance > 0 else 0,
            "positions_count": len(self.positions),
            "max_positions": self.config.max_positions,
            "risk_level": self.get_risk_level().value,
            "daily_stats": self.daily_stats.get(date_key),
            "consecutive_losses": self._consecutive_losses,
            "positions": [p.to_dict() for p in self.positions.values()]
        }
    
    def reset_daily_stats(self) -> None:
        """일일 통계 초기화"""
        today = datetime.now().date()
        date_key = today.isoformat()
        
        # 어제以前的 통계 삭제
        keys_to_delete = [
            k for k in self.daily_stats.keys()
            if k < date_key
        ]
        for k in keys_to_delete:
            del self.daily_stats[k]
        
        logger.info(f"Daily stats reset, kept {len(self.daily_stats)} days")
