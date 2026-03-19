"""
Gemini AI 전략
Gemini를 사용한 AI 기반 거래 신호 생성
"""
from typing import Dict, List, Optional, Any
from datetime import datetime

from ..gemini_client import GeminiClient, GeminiConfig, AIResponse
from ..context_builder import ContextBuilder, create_context_builder
from ...analysis.strategies.base import BaseStrategy
from ...analysis.signal_generator import TradingSignal, SignalType


class GeminiStrategy(BaseStrategy):
    """
    Gemini AI 기반 전략
    AI가 직접 BUY/SELL/HOLD 신호를 생성
    """
    
    def __init__(
        self,
        config: Optional[Dict] = None,
        gemini_config: Optional[GeminiConfig] = None
    ):
        """
        Args:
            config: 전략 설정 (전체 config 또는 ai 섹션)
            gemini_config: Gemini 설정
        """
        super().__init__("GeminiAI", config)
        ai_config = (config or {}).get("ai", config or {})
        
        # Gemini 클라이언트
        if gemini_config:
            self.gemini = GeminiClient(gemini_config)
        else:
            self.gemini = GeminiClient()
        
        # 컨텍스트 빌더 (전체 config 전달로 config["ai"] 접근)
        self.context_builder = create_context_builder(config)
        
        # 설정
        self.confidence_threshold = ai_config.get("confidence_threshold", 0.6)
        self.cache_enabled = ai_config.get("cache_enabled", True)
        
        # 마지막 신호 캐시
        self._last_signal_time: Dict[str, datetime] = {}
        self._signal_interval_seconds = ai_config.get("signal_interval", 60)
    
    @property
    def is_configured(self) -> bool:
        """Gemini API 설정 여부"""
        return self.gemini.is_configured
    
    def get_required_data_points(self) -> int:
        """필요한 최소 데이터 포인트"""
        return 20  # 최소 20개 가격 데이터
    
    async def generate_signal(
        self,
        symbol: str,
        prices: List[float],
        indicators_data: Optional[Dict] = None
    ) -> TradingSignal:
        """
        AI 기반 신호 생성
        
        Args:
            symbol: 심볼
            prices: 가격 리스트
            indicators_data: 미리 계산된 지표 데이터
        
        Returns:
            TradingSignal: 거래 신호
        """
        if not self.validate_data(prices):
            return self._create_hold_signal(
                symbol,
                prices[-1] if prices else 0,
                "데이터 부족"
            )
        
        # Rate limiting (신호 생성 간격)
        if not self._can_generate_signal(symbol):
            return self._create_hold_signal(
                symbol,
                prices[-1],
                "신호 생성 간격 제한"
            )
        
        # API 미설정 시
        if not self.is_configured:
            return self._create_hold_signal(
                symbol,
                prices[-1],
                "Gemini API 미설정"
            )
        
        try:
            # 컨텍스트 생성
            current_price = prices[-1] if prices else 0
            indicators_summary = self.context_builder.format_indicators(indicators_data)
            
            # Gemini 호출
            ai_response = await self.gemini.generate_signal(
                symbol=symbol,
                current_price=current_price,
                indicators_summary=indicators_summary,
                recent_prices=prices
            )
            
            # 응답을 TradingSignal으로 변환
            signal = self._convert_response_to_signal(
                symbol=symbol,
                response=ai_response,
                price=current_price,
                indicators_data=indicators_data
            )
            
            # 신호 시간 기록
            self._last_signal_time[symbol] = datetime.now()
            
            return signal
            
        except Exception as e:
            # 오류 시 HOLD 반환
            return self._create_hold_signal(
                symbol,
                prices[-1] if prices else 0,
                f"AI 오류: {str(e)}"
            )
    
    def _can_generate_signal(self, symbol: str) -> bool:
        """신호 생성 가능 여부 (Rate Limiting)"""
        if symbol not in self._last_signal_time:
            return True
        
        elapsed = (datetime.now() - self._last_signal_time[symbol]).total_seconds()
        return elapsed >= self._signal_interval_seconds
    
    def _convert_response_to_signal(
        self,
        symbol: str,
        response: AIResponse,
        price: float,
        indicators_data: Optional[Dict]
    ) -> TradingSignal:
        """AI 응답을 TradingSignal으로 변환"""
        # 액션 매핑
        action_map = {
            "BUY": SignalType.BUY,
            "SELL": SignalType.SELL,
            "HOLD": SignalType.HOLD
        }
        
        signal_type = action_map.get(
            response.suggested_action.upper(),
            SignalType.HOLD
        )
        
        # 신뢰도 기반 필터
        strength = response.confidence
        if response.confidence < self.confidence_threshold:
            # 신뢰도 낮으면 HOLD로 변경
            signal_type = SignalType.HOLD
        
        return TradingSignal(
            symbol=symbol,
            signal_type=signal_type,
            strength=strength,
            price=price,
            reason=f"[Gemini AI] {response.reason}",
            indicators=indicators_data or {},
            metadata={
                "ai_sentiment": response.sentiment,
                "ai_confidence": response.confidence,
                "ai_reason": response.reason,
                "source": "gemini"
            }
        )
    
    def _create_hold_signal(
        self,
        symbol: str,
        price: float,
        reason: str
    ) -> TradingSignal:
        """HOLD 신호 생성"""
        return TradingSignal(
            symbol=symbol,
            signal_type=SignalType.HOLD,
            strength=0.0,
            price=price,
            reason=reason,
            metadata={"source": "gemini_fallback"}
        )
    
    def reset(self) -> None:
        """전략 상태 초기화"""
        self._last_signal_time.clear()
        self.gemini.clear_cache()
