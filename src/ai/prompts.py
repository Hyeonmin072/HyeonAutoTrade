"""
프롬프트 템플릿
Gemini AI를 위한 프롬프트 템플릿 (한국어)
"""
from dataclasses import dataclass


@dataclass
class PromptTemplates:
    """프롬프트 템플릿 컬렉션"""
    
    # 시스템 프롬프트
    SYSTEM_PROMPT = """당신은 전문 암호화폐 거래 분석가입니다.
한국어 사용자 입력을 처리하고 정확한 JSON 응답을 제공합니다.
모든 응답은 지정된 JSON 형식으로만 작성해야 합니다.
핵심 원칙은 자본 보전이며, 애매하면 반드시 HOLD를 선택하세요.
신호 근거가 약하거나 지표 간 충돌이 있으면 공격적 추정보다 보수적 판단을 우선하세요."""

    # 시장 분석 프롬프트
    market_analysis = """
# 시장 분석 요청

## 심볼 정보
- 심볼: {symbol}
- 현재가: {price} KRW

## 기술적 지표
{indicators}

## 최근 가격 추이 (최근 20개)
{recent_prices}

{market_context}
## 지시사항
1. 시장 분위기(sentiment)를 분석하세요: bullish, bearish, neutral
2. 신뢰도(confidence)를 0.0~1.0으로 평가하세요
3. 분석 근거를 간단히 설명하세요
4. 추천 행동(suggested_action)을 BUY, SELL, HOLD 중 하나를 선택하세요

## 출력 형식 (JSON만 응답)
{{
  "sentiment": "bullish|bearish|neutral",
  "confidence": 0.0~1.0,
  "reason": "분석 근거 설명",
  "suggested_action": "BUY|SELL|HOLD"
}}
"""

    # 신호 생성 프롬프트
    signal_generation = """
# 거래 신호 생성 요청

당신은 암호화폐 거래 신호를 생성하는 전문가입니다.

## 현재 상황
- 심볼: {symbol}
- 현재가: {price} KRW

## 기술적 지표 분석
{indicators}

## 최근 가격 추이 (최근 30개)
{recent_prices}

## 현재 포지션
{position_info}

## 지시사항
1. 위 데이터를 기반으로 매수/매도/보유 신호를 생성하세요
2. 신뢰도(confidence)를 0.0~1.0으로 평가하세요
3. 신호 결정 이유를 명확히 설명하세요
4. 신호 강도(strength)를 고려하여confidence를 조정하세요
5. 지표가 상충하거나 추세가 불명확하면 HOLD를 선택하세요
6. 단기 급등 추격 매수는 피하고, 손익비가 불리하면 HOLD를 선택하세요

## 출력 형식 (JSON만 응답)
{{
  "sentiment": "bullish|bearish|neutral",
  "confidence": 0.0~1.0,
  "reason": "신호 결정 이유",
  "suggested_action": "BUY|SELL|HOLD"
}}

주의: 신호가 BUY이면 RSI가 과매도 구간이거나 MACD 골든크로스 등 상승 신호가 있는 경우, SELL이면 과매수 구간이거나 하락 신호가 있는 경우에만 생성하세요.
추가 주의: 충분한 정량 근거가 없으면 반드시 HOLD를 반환하세요.
"""

    # 신호 검증 프롬프트
    signal_validation = """
# 거래 신호 검증 요청

규칙 기반 시스템이 "{action}" 신호를 생성했습니다.
이 신호가 현재 시장 상황에서 적절한지 검증해 주세요.

## 심볼 정보
- 심볼: {symbol}
- 현재가: {price} KRW

## 기술적 지표
{indicators}

## 시장 요약
{market}

## 지시사항
1. 이 신호에 동의하는지 여부(agreed)를 표시하세요
2. 신뢰도(confidence)를 0.0~1.0으로 평가하세요
3. 동의하지 않는다면 대안 신호(alternative_action)를 제시하세요
4. 판단 근거를 설명하세요
5. 손실 위험이 크거나 모호하면 alternative_action은 HOLD를 우선 제시하세요

## 출력 형식 (JSON만 응답)
{{
  "agreed": true|false,
  "confidence": 0.0~1.0,
  "reason": "판단 근거",
  "alternative_action": "BUY|SELL|HOLD|null"
}}
"""

    # 리스크 평가 프롬프트
    risk_assessment = """
# 리스크 평가 요청

현재 포지션의 리스크를 평가해 주세요.

## 포지션 정보
- 심볼: {symbol}
- 현재가: {price} KRW
- 포지션 크기: {position_size} KRW
- 미실현 손익: {pnl} KRW

## 시장 상황
{market}

## 지시사항
1. 현재 포지션의 위험 수준을 평가하세요
2. 시장 상황을 고려한 리스크 등급을 제시하세요
3. 필요하다면 손절/익절/유지 권고를 제시하세요

## 출력 형식 (JSON만 응답)
{{
  "sentiment": "high_risk|moderate|low_risk",
  "confidence": 0.0~1.0,
  "reason": "평가 근거",
  "suggested_action": "HOLD|REDUCE|CLOSE"
}}
"""


# 템플릿 포맷 헬퍼
def format_market_analysis(
    symbol: str,
    price: float,
    indicators: str,
    recent_prices: list,
    market_context: str = ""
) -> str:
    """시장 분석 프롬프트 포맷"""
    return PromptTemplates.market_analysis.format(
        symbol=symbol,
        price=price,
        indicators=indicators,
        recent_prices=", ".join([f"{p:.2f}" for p in recent_prices[-20:]]),
        market_context=market_context
    )


def format_signal_generation(
    symbol: str,
    price: float,
    indicators: str,
    recent_prices: list,
    position_info: str = "없음"
) -> str:
    """신호 생성 프롬프트 포맷"""
    return PromptTemplates.signal_generation.format(
        symbol=symbol,
        price=price,
        indicators=indicators,
        recent_prices=", ".join([f"{p:.2f}" for p in recent_prices[-30:]]),
        position_info=position_info
    )
