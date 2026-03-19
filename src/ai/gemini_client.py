"""
Gemini AI 클라이언트
Google Gemini API를 통한 시장 분석 및 신호 생성
"""
import os
import json
import asyncio
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import httpx

from ..monitoring.logger import get_logger


logger = get_logger("gemini_client")


class GeminiMode(Enum):
    """Gemini 동작 모드"""
    PRIMARY = "primary"      # AI가 신호를 전부 생성
    ADVISOR = "advisor"      # AI가 기존 신호를 검토
    HYBRID = "hybrid"         # 규칙 + AI 조합


@dataclass
class AIResponse:
    """AI 응답"""
    sentiment: str  # bullish, bearish, neutral
    confidence: float  # 0.0 ~ 1.0
    reason: str
    suggested_action: str  # BUY, SELL, HOLD
    raw_response: Optional[Dict] = None
    token_usage: Optional[Dict] = None


@dataclass
class SignalValidation:
    """신호 검증 결과"""
    agreed: bool
    confidence: float
    alternative_action: Optional[str] = None
    reason: str = ""


@dataclass
class GeminiConfig:
    """Gemini 설정"""
    api_key: Optional[str] = None
    model: str = "gemini-1.5-flash"
    mode: GeminiMode = GeminiMode.HYBRID
    rate_limit_per_minute: int = 15
    max_tokens_per_request: int = 1024
    temperature: float = 0.7
    timeout: int = 30
    cache_ttl_seconds: int = 60  # 캐시 TTL


class GeminiClient:
    """
    Gemini AI 클라이언트
    비동기 API 호출, Rate Limiting, 캐싱 지원
    """
    
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
    
    def __init__(self, config: Optional[GeminiConfig] = None):
        """
        Args:
            config: Gemini 설정
        """
        self.config = config or GeminiConfig()
        
        # API 키 로드
        if not self.config.api_key:
            self.config.api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_AI_API_KEY")
        
        if not self.config.api_key:
            logger.warning("Gemini API key not found. Set GEMINI_API_KEY or GOOGLE_AI_API_KEY")
        
        # Rate Limiting
        self._request_times: List[float] = []
        self._lock = asyncio.Lock()
        
        # 캐시
        self._cache: Dict[str, tuple] = {}  # key -> (response, timestamp)
        
        logger.info(f"GeminiClient initialized with model: {self.config.model}")
    
    @property
    def is_configured(self) -> bool:
        """API 키 설정 여부"""
        return bool(self.config.api_key)
    
    async def _check_rate_limit(self) -> None:
        """Rate Limit 체크"""
        async with self._lock:
            now = time.time()
            # 1분 이내 요청 필터링
            self._request_times = [t for t in self._request_times if now - t < 60]
            
            if len(self._request_times) >= self.config.rate_limit_per_minute:
                sleep_time = 60 - (now - self._request_times[0]) + 1
                logger.warning(f"Rate limit reached, sleeping for {sleep_time:.1f}s")
                await asyncio.sleep(sleep_time)
            
            self._request_times.append(time.time())
    
    def _get_cache(self, key: str) -> Optional[AIResponse]:
        """캐시 조회"""
        if key in self._cache:
            response, timestamp = self._cache[key]
            if time.time() - timestamp < self.config.cache_ttl_seconds:
                return response
            del self._cache[key]
        return None
    
    def _set_cache(self, key: str, response: AIResponse) -> None:
        """캐시 저장"""
        self._cache[key] = (response, time.time())
    
    def _build_cache_key(self, prompt_type: str, context: Dict) -> str:
        """캐시 키 생성"""
        symbol = context.get("symbol", "")
        return f"{prompt_type}:{symbol}:{len(context.get('recent_prices', []))}"
    
    async def _call_api(
        self,
        prompt: str,
        system_instruction: Optional[str] = None
    ) -> Dict:
        """Gemini API 호출"""
        if not self.is_configured:
            raise ValueError("Gemini API key not configured")
        
        await self._check_rate_limit()
        
        url = f"{self.BASE_URL}/{self.config.model}:generateContent"
        
        headers = {
            "Content-Type": "application/json"
        }
        
        contents = [{"parts": [{"text": prompt}]}]
        
        payload = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": self.config.max_tokens_per_request,
                "temperature": self.config.temperature
            }
        }
        
        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }
        
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout) as client:
                response = await client.post(
                    url,
                    headers=headers,
                    params={"key": self.config.api_key},
                    json=payload
                )
                
                response.raise_for_status()
                data = response.json()
                
                # 응답 파싱
                if "candidates" in data and len(data["candidates"]) > 0:
                    text = data["candidates"][0]["content"]["parts"][0]["text"]
                    return {"text": text, "raw": data}
                else:
                    raise ValueError("Invalid response format")
                    
        except httpx.HTTPStatusError as e:
            logger.error(f"Gemini API HTTP error: {e.response.status_code}")
            raise
        except Exception as e:
            logger.error(f"Gemini API error: {e}")
            raise
    
    async def _parse_response(self, text: str) -> AIResponse:
        """응답 텍스트 파싱 (JSON)"""
        try:
            # JSON 블록 추출
            if "```json" in text:
                start = text.find("```json") + 7
                end = text.find("```", start)
                text = text[start:end].strip()
            elif "```" in text:
                start = text.find("```") + 3
                end = text.find("```", start)
                text = text[start:end].strip()
            
            data = json.loads(text)
            
            return AIResponse(
                sentiment=data.get("sentiment", "neutral"),
                confidence=float(data.get("confidence", 0.5)),
                reason=data.get("reason", ""),
                suggested_action=data.get("suggested_action", "HOLD"),
                raw_response=data
            )
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}")
            # Fallback: 텍스트에서 키워드 추출
            text_lower = text.lower()
            if "buy" in text_lower and "sell" not in text_lower:
                action = "BUY"
            elif "sell" in text_lower:
                action = "SELL"
            else:
                action = "HOLD"
            
            return AIResponse(
                sentiment="neutral",
                confidence=0.5,
                reason=text[:200],
                suggested_action=action,
                raw_response={"text": text}
            )
    
    async def analyze_market(
        self,
        symbol: str,
        current_price: float,
        indicators_summary: str,
        recent_prices: List[float],
        market_context: Optional[str] = None
    ) -> AIResponse:
        """
        시장 분석
        
        Args:
            symbol: 심볼
            current_price: 현재가
            indicators_summary: 지표 요약
            recent_prices: 최근 가격 리스트
            market_context: 추가 시장 맥락
        
        Returns:
            AIResponse: 시장 분석 결과
        """
        from .prompts import PromptTemplates
        
        cache_key = self._build_cache_key("analyze", {
            "symbol": symbol,
            "recent_prices": recent_prices
        })
        
        # 캐시 확인
        cached = self._get_cache(cache_key)
        if cached:
            logger.debug(f"Cache hit for {cache_key}")
            return cached
        
        prompt = PromptTemplates.market_analysis.format(
            symbol=symbol,
            price=current_price,
            indicators=indicators_summary,
            recent_prices=", ".join([f"{p:.2f}" for p in recent_prices[-20:]]),
            market_context=market_context or ""
        )
        
        try:
            response = await self._call_api(prompt)
            result = await self._parse_response(response["text"])
            
            # 캐시 저장
            self._set_cache(cache_key, result)
            
            logger.info(f"Market analysis for {symbol}: {result.sentiment} ({result.confidence:.2f})")
            return result
            
        except Exception as e:
            logger.error(f"Market analysis failed: {e}")
            return AIResponse(
                sentiment="neutral",
                confidence=0.0,
                reason=f"API 오류: {str(e)}",
                suggested_action="HOLD"
            )
    
    async def generate_signal(
        self,
        symbol: str,
        current_price: float,
        indicators_summary: str,
        recent_prices: List[float],
        position_info: Optional[str] = None
    ) -> AIResponse:
        """
        신호 생성
        
        Args:
            symbol: 심볼
            current_price: 현재가
            indicators_summary: 지표 요약
            recent_prices: 최근 가격 리스트
            position_info: 포지션 정보
        
        Returns:
            AIResponse: 거래 신호
        """
        from .prompts import PromptTemplates
        
        cache_key = self._build_cache_key("signal", {
            "symbol": symbol,
            "recent_prices": recent_prices
        })
        
        # 캐시 확인
        cached = self._get_cache(cache_key)
        if cached:
            logger.debug(f"Cache hit for {cache_key}")
            return cached
        
        prompt = PromptTemplates.signal_generation.format(
            symbol=symbol,
            price=current_price,
            indicators=indicators_summary,
            recent_prices=", ".join([f"{p:.2f}" for p in recent_prices[-30:]]),
            position_info=position_info or "없음"
        )
        
        try:
            response = await self._call_api(prompt)
            result = await self._parse_response(response["text"])
            
            # 캐시 저장
            self._set_cache(cache_key, result)
            
            logger.info(f"AI Signal for {symbol}: {result.suggested_action} ({result.confidence:.2f})")
            return result
            
        except Exception as e:
            logger.error(f"Signal generation failed: {e}")
            return AIResponse(
                sentiment="neutral",
                confidence=0.0,
                reason=f"API 오류: {str(e)}",
                suggested_action="HOLD"
            )
    
    async def validate_signal(
        self,
        rule_signal: str,  # BUY, SELL, HOLD
        symbol: str,
        current_price: float,
        indicators_summary: str,
        market_summary: str
    ) -> SignalValidation:
        """
        규칙 기반 신호 검증
        
        Args:
            rule_signal: 규칙 신호
            symbol: 심볼
            current_price: 현재가
            indicators_summary: 지표 요약
            market_summary: 시장 요약
        
        Returns:
            SignalValidation: 검증 결과
        """
        from .prompts import PromptTemplates
        
        prompt = PromptTemplates.signal_validation.format(
            action=rule_signal,
            symbol=symbol,
            price=current_price,
            indicators=indicators_summary,
            market=market_summary
        )
        
        try:
            response = await self._call_api(prompt)
            
            # JSON 파싱
            if "```json" in response["text"]:
                start = response["text"].find("```json") + 7
                end = response["text"].find("```", start)
                text = response["text"][start:end].strip()
            else:
                text = response["text"]
            
            data = json.loads(text)
            
            agreed = data.get("agreed", True)
            confidence = float(data.get("confidence", 0.5))
            reason = data.get("reason", "")
            alternative = data.get("alternative_action")
            
            result = SignalValidation(
                agreed=agreed,
                confidence=confidence,
                alternative_action=alternative,
                reason=reason
            )
            
            logger.info(f"Signal validation for {symbol}: agreed={agreed}, conf={confidence:.2f}")
            return result
            
        except Exception as e:
            logger.error(f"Signal validation failed: {e}")
            # 오류 시 기본값 (동의)
            return SignalValidation(
                agreed=True,
                confidence=0.0,
                reason=f"API 오류: {str(e)}"
            )
    
    async def assess_risk(
        self,
        symbol: str,
        current_price: float,
        position_size: float,
        unrealized_pnl: float,
        market_summary: str
    ) -> AIResponse:
        """
        리스크 평가
        
        Args:
            symbol: 심볼
            current_price: 현재가
            position_size: 포지션 크기
            unrealized_pnl: 미실현 손익
            market_summary: 시장 요약
        
        Returns:
            AIResponse: 리스크 평가 결과
        """
        from .prompts import PromptTemplates
        
        prompt = PromptTemplates.risk_assessment.format(
            symbol=symbol,
            price=current_price,
            position_size=position_size,
            pnl=unrealized_pnl,
            market=market_summary
        )
        
        try:
            response = await self._call_api(prompt)
            result = await self._parse_response(response["text"])
            
            logger.info(f"Risk assessment for {symbol}: {result.sentiment}")
            return result
            
        except Exception as e:
            logger.error(f"Risk assessment failed: {e}")
            return AIResponse(
                sentiment="neutral",
                confidence=0.0,
                reason=f"API 오류: {str(e)}",
                suggested_action="HOLD"
            )
    
    def clear_cache(self) -> None:
        """캐시 초기화"""
        self._cache.clear()
        logger.info("Gemini cache cleared")
    
    def get_stats(self) -> Dict:
        """통계 반환"""
        return {
            "model": self.config.model,
            "is_configured": self.is_configured,
            "cache_size": len(self._cache),
            "requests_in_last_minute": len(self._request_times)
        }
