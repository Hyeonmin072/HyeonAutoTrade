"""
하이브리드 전략
규칙 기반 + AI 검증/보완
"""
from typing import Dict, List, Optional, Any
from datetime import datetime

from ...analysis.strategies.base import BaseStrategy
from ...analysis.signal_generator import TradingSignal, SignalType
from ..gemini_client import GeminiClient, GeminiConfig, AIResponse
from ..context_builder import ContextBuilder, create_context_builder


class HybridStrategy(BaseStrategy):
    """
    하이브리드 전략
    규칙 기반 신호 + AI 검증/보완
    """
    
    def __init__(
        self,
        rule_strategy: BaseStrategy,
        gemini_config: Optional[GeminiConfig] = None,
        config: Optional[Dict] = None
    ):
        """
        Args:
            rule_strategy: 규칙 기반 전략 (RSI, MACD 등)
            gemini_config: Gemini 설정
            config: 전략 설정
        """
        super().__init__("Hybrid", config)
        ai_config = (config or {}).get("ai", config or {})
        
        self.rule_strategy = rule_strategy
        self.gemini = GeminiClient(gemini_config)
        self.context_builder = create_context_builder(config)
        
        # 설정
        self.ai_weight = ai_config.get("ai_weight", 0.5)
        self.confidence_threshold = ai_config.get("confidence_threshold", 0.6)
        self.conflict_action = ai_config.get("conflict_action", "hold")  # hold, follow_rule, follow_ai
        self.require_ai_validation = ai_config.get("require_ai_validation", True)
        
        # 마지막 검증 시간
        self._last_validation: Dict[str, datetime] = {}
        self._validation_interval = ai_config.get("validation_interval", 300)  # 5분
    
    @property
    def is_configured(self) -> bool:
        """Gemini API 설정 여부"""
        return self.gemini.is_configured
    
    def get_required_data_points(self) -> int:
        """필요한 최소 데이터 포인트 (규칙 전략에 따름)"""
        return self.rule_strategy.get_required_data_points()
    
    async def generate_signal(
        self,
        symbol: str,
        prices: List[float],
        indicators_data: Optional[Dict] = None
    ) -> TradingSignal:
        """
        하이브리드 신호 생성
        
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
        
        # 1단계: 규칙 기반 신호 생성
        rule_signal = self.rule_strategy.generate_signal(symbol, prices, indicators_data)
        
        # 규칙 신호가 실행 불가능하면 그대로 반환
        if not rule_signal.is_actionable:
            return rule_signal
        
        # 2단계: AI 미설정 시 규칙 신호만 반환
        if not self.is_configured:
            rule_signal.reason = f"[Rule] {rule_signal.reason}"
            rule_signal.metadata["source"] = "rule_only"
            return rule_signal
        
        # 3단계: AI 검증
        ai_validation = await self._validate_with_ai(
            symbol=symbol,
            prices=prices,
            rule_signal=rule_signal,
            indicators_data=indicators_data
        )
        
        # 4단계: 신호 결합
        return self._merge_signals(
            symbol=symbol,
            rule_signal=rule_signal,
            ai_response=ai_validation,
            price=prices[-1] if prices else 0,
            indicators_data=indicators_data
        )
    
    async def _validate_with_ai(
        self,
        symbol: str,
        prices: List[float],
        rule_signal: TradingSignal,
        indicators_data: Optional[Dict]
    ) -> Optional[AIResponse]:
        """
        AI로 규칙 신호 검증
        
        Args:
            symbol: 심볼
            prices: 가격 리스트
            rule_signal: 규칙 기반 신호
            indicators_data: 지표 데이터
        
        Returns:
            AIResponse 또는 None
        """
        # 검증 간격 제한
        if not self._can_validate(symbol):
            return None
        
        try:
            current_price = prices[-1] if prices else 0
            indicators_summary = self.context_builder.format_indicators(indicators_data)
            
            # AI에 검증 요청 (SignalValidation 반환)
            validation_result = await self.gemini.validate_signal(
                rule_signal=rule_signal.signal_type.value.upper(),
                symbol=symbol,
                current_price=current_price,
                indicators_summary=indicators_summary,
                market_summary=f"현재 추세: {rule_signal.reason}"
            )
            
            # AIResponse로 변환
            response = AIResponse(
                sentiment="bullish" if validation_result.agreed else "bearish",
                confidence=validation_result.confidence,
                reason=validation_result.reason,
                suggested_action=(
                    validation_result.alternative_action
                    if validation_result.alternative_action and not validation_result.agreed
                    else rule_signal.signal_type.value.upper()
                )
            )
            
            self._last_validation[symbol] = datetime.now()
            return response
            
        except Exception as e:
            return None
    
    def _can_validate(self, symbol: str) -> bool:
        """AI 검증 가능 여부"""
        if symbol not in self._last_validation:
            return True
        
        elapsed = (datetime.now() - self._last_validation[symbol]).total_seconds()
        return elapsed >= self._validation_interval
    
    def _merge_signals(
        self,
        symbol: str,
        rule_signal: TradingSignal,
        ai_response: Optional[AIResponse],
        price: float,
        indicators_data: Optional[Dict]
    ) -> TradingSignal:
        """
        규칙 신호 + AI 검증 결합
        
        Args:
            symbol: 심볼
            rule_signal: 규칙 기반 신호
            ai_response: AI 검증 결과
            price: 현재가
            indicators_data: 지표 데이터
        
        Returns:
            TradingSignal: 최종 신호
        """
        # AI 응답 없으면 규칙 신호만 사용
        if ai_response is None:
            rule_signal.reason = f"[Rule Only] {rule_signal.reason}"
            rule_signal.metadata["source"] = "rule_only"
            return rule_signal
        
        # 신호가 같은 방향이면 강화
        rule_action = rule_signal.signal_type.value.upper()
        ai_action = ai_response.suggested_action.upper()
        
        if rule_action == ai_action:
            # 신호 일치: 신뢰도 평균
            new_strength = (rule_signal.strength + ai_response.confidence) / 2
            reason = f"[Hybrid Agree] {rule_signal.reason} + AI 동의 ({ai_response.reason})"
            
            return TradingSignal(
                symbol=symbol,
                signal_type=rule_signal.signal_type,
                strength=new_strength,
                price=price,
                reason=reason,
                indicators=indicators_data or {},
                metadata={
                    "rule_strength": rule_signal.strength,
                    "ai_confidence": ai_response.confidence,
                    "ai_reason": ai_response.reason,
                    "source": "hybrid_agree"
                }
            )
        
        # 신호 충돌
        else:
            if self.conflict_action == "hold":
                # 충돌 시 HOLD
                return TradingSignal(
                    symbol=symbol,
                    signal_type=SignalType.HOLD,
                    strength=0.0,
                    price=price,
                    reason=f"[Hybrid Conflict] Rule: {rule_action}, AI: {ai_action} - HOLD",
                    indicators=indicators_data or {},
                    metadata={
                        "rule_signal": rule_signal.signal_type.value,
                        "ai_suggestion": ai_action,
                        "ai_reason": ai_response.reason,
                        "source": "hybrid_conflict"
                    }
                )
            
            elif self.conflict_action == "follow_rule":
                # 규칙 따르기
                rule_signal.reason = f"[Hybrid Override] {rule_signal.reason} (AI 반대: {ai_response.reason})"
                rule_signal.metadata["source"] = "hybrid_rule_override"
                return rule_signal
            
            elif self.conflict_action == "follow_ai":
                # AI 따르기
                action_map = {
                    "BUY": SignalType.BUY,
                    "SELL": SignalType.SELL,
                    "HOLD": SignalType.HOLD
                }
                new_type = action_map.get(ai_action, SignalType.HOLD)
                
                return TradingSignal(
                    symbol=symbol,
                    signal_type=new_type,
                    strength=ai_response.confidence,
                    price=price,
                    reason=f"[Hybrid AI Override] {ai_response.reason}",
                    indicators=indicators_data or {},
                    metadata={
                        "rule_signal": rule_signal.signal_type.value,
                        "ai_suggestion": ai_action,
                        "source": "hybrid_ai_override"
                    }
                )
        
        # 기본: 규칙 따르기
        rule_signal.metadata["source"] = "hybrid_default"
        return rule_signal
    
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
            metadata={"source": "hybrid_fallback"}
        )
    
    def reset(self) -> None:
        """전략 상태 초기화"""
        self._last_validation.clear()
        if hasattr(self.rule_strategy, 'reset'):
            self.rule_strategy.reset()
        self.gemini.clear_cache()
