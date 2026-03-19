"""
컨텍스트 빌더
Gemini AI 입력을 위한 데이터 컨텍스트 생성
"""
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime

from ..monitoring.logger import get_logger


logger = get_logger("context_builder")


@dataclass
class MarketContext:
    """시장 컨텍스트 데이터"""
    symbol: str
    current_price: float
    price_change_24h: float = 0.0
    indicators_summary: str = ""
    recent_prices: List[float] = field(default_factory=list)
    position_info: Optional[str] = None
    token_budget: int = 2000  # 토큰 예산 (대략적)


class ContextBuilder:
    """
    컨텍스트 빌더
    시세·지표·포지션 데이터를 AI 입력용으로 변환
    """
    
    # 각 가격의 대략적 토큰 수 (한글 문자는 더 많음)
    TOKENS_PER_PRICE = 3
    TOKENS_PER_CHAR = 2
    
    def __init__(self, token_budget: int = 2000):
        """
        Args:
            token_budget: 프롬프트 토큰 예산
        """
        self.token_budget = token_budget
    
    def build(
        self,
        symbol: str,
        prices: List[float],
        current_price: float,
        indicators_data: Optional[Dict] = None,
        position_info: Optional[str] = None,
        price_change_24h: float = 0.0
    ) -> MarketContext:
        """
        시장 컨텍스트 생성
        
        Args:
            symbol: 심볼
            prices: 최근 가격 리스트
            current_price: 현재가
            indicators_data: 지표 데이터 딕셔너리
            position_info: 포지션 정보 문자열
            price_change_24h: 24시간 변동률
        
        Returns:
            MarketContext: 시장 컨텍스트
        """
        # 지표 요약 생성
        indicators_summary = self.format_indicators(indicators_data)
        
        # 가격 리스트 트렁케이션
        truncated_prices = self.format_prices(prices)
        
        return MarketContext(
            symbol=symbol,
            current_price=current_price,
            price_change_24h=price_change_24h,
            indicators_summary=indicators_summary,
            recent_prices=truncated_prices,
            position_info=position_info,
            token_budget=self.token_budget
        )
    
    def format_indicators(self, indicators_data: Optional[Dict]) -> str:
        """
        지표 데이터 포맷팅
        
        Args:
            indicators_data: 지표 딕셔너리
        
        Returns:
            포맷된 지표 문자열
        """
        if not indicators_data:
            return "지표 데이터 없음"
        
        parts = []
        
        # RSI
        if "rsi" in indicators_data:
            rsi = indicators_data["rsi"]
            if hasattr(rsi, "rsi") and rsi.rsi is not None:
                parts.append(f"RSI: {rsi.rsi:.1f}")
                if rsi.rsi < 30:
                    parts.append("(과매도)")
                elif rsi.rsi > 70:
                    parts.append("(과매수)")
            elif isinstance(rsi, dict):
                rsi_val = rsi.get("rsi", rsi.get("value", 0))
                parts.append(f"RSI: {rsi_val:.1f}")
        
        # MACD
        if "macd" in indicators_data:
            macd = indicators_data["macd"]
            if hasattr(macd, "macd") and macd.macd is not None:
                parts.append(f"MACD: {macd.macd:.4f}")
                if macd.histogram is not None and macd.histogram > 0:
                    parts.append("(상승)")
                else:
                    parts.append("(하락)")
            elif isinstance(macd, dict):
                hist = macd.get("histogram", 0) or 0
                parts.append(f"MACD 히스토그램: {hist:.4f}")
                parts.append("(상승)" if hist > 0 else "(하락)")
        
        # Bollinger Bands
        if "bollinger_bands" in indicators_data:
            bb = indicators_data["bollinger_bands"]
            if hasattr(bb, "position") and bb.position is not None:
                parts.append(f"BB 위치: {bb.position:.2%}")
                if bb.position < 0.2:
                    parts.append("(하단 밴드 근처)")
                elif bb.position > 0.8:
                    parts.append("(상단 밴드 근처)")
            elif isinstance(bb, dict):
                pos = bb.get("position", 0.5) or 0.5
                parts.append(f"BB 위치: {pos:.2%}")
        
        # Moving Averages
        if "ma" in indicators_data:
            ma = indicators_data["ma"]
            if isinstance(ma, dict):
                for period, ma_data in ma.items():
                    if hasattr(ma_data, "ma"):
                        parts.append(f"MA{period}: {ma_data.ma:.2f}")
                    elif isinstance(ma_data, dict):
                        parts.append(f"MA{period}: {ma_data.get('ma', 0):.2f}")
        
        if not parts:
            return "지표: 데이터 형식 인식 불가"
        
        return ", ".join(parts)
    
    def format_prices(self, prices: List[float]) -> List[float]:
        """
        가격 리스트 트렁케이션 (토큰 예산 내에서)
        
        Args:
            prices: 전체 가격 리스트
        
        Returns:
            트렁케이션된 가격 리스트
        """
        if not prices:
            return []
        
        # 대략적 토큰 계산
        # 헤더 텍스트 (심볼, 현재가 등): ~100 토큰
        # 지표 텍스트: ~200 토큰
        # 남은 예산
        available_for_prices = self.token_budget - 300
        
        max_prices = available_for_prices // self.TOKENS_PER_PRICE
        max_prices = min(max_prices, 100)  # 최대 100개
        
        if len(prices) <= max_prices:
            return prices
        
        # 최근 prices만 반환
        return prices[-max_prices:]
    
    def format_prices_string(self, prices: List[float], format_type: str = "list") -> str:
        """
        가격 리스트를 문자열로 포맷팅
        
        Args:
            prices: 가격 리스트
            format_type: list(쉼표 구분), trend(화살표), candle(OHLC)
        
        Returns:
            포맷된 문자열
        """
        if not prices:
            return "가격 데이터 없음"
        
        truncated = self.format_prices(prices)
        
        if format_type == "list":
            return ", ".join([f"{p:.2f}" for p in truncated])
        
        elif format_type == "trend":
            # 추세 화살표 추가
            if len(truncated) >= 2:
                if truncated[-1] > truncated[-2]:
                    trend = "↑"
                elif truncated[-1] < truncated[-2]:
                    trend = "↓"
                else:
                    trend = "→"
                return f"{truncated[-1]:.2f} {trend}"
            return f"{truncated[-1]:.2f}"
        
        elif format_type == "candle" and len(truncated) >= 4:
            # 캔들 형태
            open_p = truncated[0]
            close_p = truncated[-1]
            high_p = max(truncated)
            low_p = min(truncated)
            
            return f"O:{open_p:.2f} H:{high_p:.2f} L:{low_p:.2f} C:{close_p:.2f}"
        
        return ", ".join([f"{p:.2f}" for p in truncated[:10]])
    
    def build_prompt_context(self, context: MarketContext) -> Dict[str, Any]:
        """
        프롬프트용 컨텍스트 딕셔너리 생성
        
        Args:
            context: 시장 컨텍스트
        
        Returns:
            프롬프트용 딕셔너리
        """
        return {
            "symbol": context.symbol,
            "price": context.current_price,
            "change_24h": f"{context.price_change_24h:+.2f}%",
            "indicators": context.indicators_summary,
            "recent_prices": self.format_prices_string(context.recent_prices, "trend"),
            "position": context.position_info or "없음"
        }
    
    def estimate_tokens(self, text: str) -> int:
        """
        텍스트 토큰 수 추정
        
        Args:
            text: 입력 텍스트
        
        Returns:
            추정 토큰 수
        """
        return len(text) * self.TOKENS_PER_CHAR


def create_context_builder(config: Optional[Dict] = None) -> ContextBuilder:
    """
    컨텍스트 빌더 팩토리
    
    Args:
        config: 설정 딕셔너리
    
    Returns:
        ContextBuilder 인스턴스
    """
    token_budget = 2000
    if config and "ai" in config:
        token_budget = config["ai"].get("max_tokens_per_request", 2000)
    
    return ContextBuilder(token_budget=token_budget)
