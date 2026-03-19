"""
기본 전략 추상 클래스
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Optional

from ..signal_generator import TradingSignal


class BaseStrategy(ABC):
    """
    거래 전략 기본 추상 클래스
    모든 커스텀 전략은 이 클래스를 상속해야 합니다.
    """
    
    def __init__(self, name: str, config: Optional[Dict] = None):
        """
        Args:
            name: 전략 이름
            config: 전략 설정
        """
        self.name = name
        self.config = config or {}
    
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
        """필요한 최소 데이터 포인트 수 반환"""
        pass
    
    def validate_data(self, prices: List[float]) -> bool:
        """데이터 유효성 검증"""
        return len(prices) >= self.get_required_data_points()
    
    def reset(self) -> None:
        """전략 상태 초기화"""
        pass
