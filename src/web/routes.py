"""
API 라우트
봇 상태, 포지션, 가격, 설정 조회
"""
from typing import Dict, List, Optional, Any

from fastapi import APIRouter, HTTPException

from .state import get_bot
from src.analysis.signal_generator import SignalGenerator


router = APIRouter()


def _require_bot():
    """봇 인스턴스 필수"""
    bot = get_bot()
    if bot is None:
        raise HTTPException(status_code=503, detail="봇이 초기화되지 않았습니다.")
    return bot


@router.get("/status")
async def get_status() -> Dict:
    """봇 상태"""
    bot = _require_bot()
    return {
        "running": bot._running,
        "shutdown_requested": bot._shutdown_requested,
        "exchange": bot.config.get("exchange", {}).get("name", "unknown"),
        "symbols": getattr(bot, "_symbols_to_monitor", None) or bot.config.get("exchange", {}).get("symbols", []),
        "scanner_enabled": getattr(bot, "_scanner_enabled", False),
        "strategy": bot.signal_generator.strategy_name,
        "dry_run": bot.config.get("mode", {}).get("dry_run", True),
        "ai_enabled": bot.config.get("ai", {}).get("enabled", False),
        "futures_mode": getattr(bot.exchange, "is_futures", False),
        "leverage": getattr(bot.exchange, "leverage", 1),
    }


@router.get("/positions")
async def get_positions() -> List[Dict]:
    """현재 포지션"""
    bot = _require_bot()
    positions = []
    for symbol, pos in bot.risk_manager.positions.items():
        # 최신 가격으로 업데이트
        latest = (bot._price_cache.get(symbol) or [])[-1]
        if latest is not None:
            pos.update(latest)
        positions.append(pos.to_dict())
    return positions


@router.get("/prices")
async def get_prices() -> Dict[str, Any]:
    """가격 캐시 및 티커 - 실시간 가격"""
    bot = _require_bot()
    result = {}
    symbols = getattr(bot, "_symbols_to_monitor", None) or bot.config.get("exchange", {}).get("symbols", [])
    use_ohlcv = getattr(bot.data_collector, "use_ohlcv_for_signals", False)
    for symbol in symbols:
        normalized = getattr(bot.data_collector, "_normalize_symbol", lambda s: s)(symbol)
        prices = bot._price_cache.get(symbol) or bot._price_cache.get(normalized, [])
        ticker = bot.data_collector.get_ticker(symbol) or bot.data_collector.get_ticker(normalized)
        ticker_data = ticker.to_dict() if ticker else None
        current = prices[-1] if prices else (ticker_data.get("price") if ticker_data else None)
        # 1분봉 모드: 최근 1분 등락률 (OHLCV close 기준)
        change_percent_1m = None
        if use_ohlcv and len(prices) >= 2 and prices[-2]:
            change_percent_1m = (prices[-1] - prices[-2]) / prices[-2] * 100
        result[symbol] = {
            "current": current,
            "history_count": len(prices),
            "change_percent_1m": change_percent_1m,
            "use_ohlcv": use_ohlcv,
            "ticker": ticker_data,
            "updated_at": ticker_data.get("timestamp") if ticker_data else None,
        }
    return result


@router.get("/config")
async def get_config() -> Dict:
    """설정 요약 (민감 정보 제외)"""
    bot = _require_bot()
    cfg = bot.config.copy()
    # API 키 등 민감 정보 제거
    if "exchange" in cfg:
        cfg["exchange"] = {k: v for k, v in cfg["exchange"].items() 
                          if k not in ("api_key", "secret", "apiKey", "secretKey")}
    return cfg


@router.get("/trades")
async def get_trades(symbol: Optional[str] = None, limit: int = 50) -> List[Dict]:
    """거래 히스토리"""
    bot = _require_bot()
    return await bot.position_store.get_trade_history(symbol=symbol, limit=limit)


@router.get("/balances")
async def get_balances() -> Dict:
    """보유 코인/잔고 조회"""
    bot = _require_bot()
    try:
        balances = await bot.exchange.fetch_balance()
        result = {}
        # 평가 기준 통화 (KRW, USDT 등) - 모니터링 중인 심볼에서 추출
        symbols = getattr(bot, "_symbols_to_monitor", None) or bot.config.get("exchange", {}).get("symbols", [])
        quote = "USDT"
        if symbols:
            try:
                quote = symbols[0].split("/")[-1]
            except Exception:
                quote = "USDT"
        for currency, b in balances.items():
            total = getattr(b, "total", 0) if hasattr(b, "total") else (b.get("total", 0) if isinstance(b, dict) else 0)
            if total > 0:
                free = getattr(b, "free", 0) if hasattr(b, "free") else (b.get("free", 0) if isinstance(b, dict) else 0)
                used = getattr(b, "used", 0) if hasattr(b, "used") else (b.get("used", 0) if isinstance(b, dict) else 0)
                # 현재가 및 평가액 (quote 기준)
                if currency == quote:
                    current_price = 1.0
                    value_quote = total
                else:
                    symbol = f"{currency}/{quote}"
                    ticker = bot.data_collector.get_ticker(symbol)
                    price = getattr(ticker, "price", None) if ticker else None
                    current_price = float(price) if price is not None else None
                    value_quote = (current_price * total) if current_price is not None else None
                result[currency] = {
                    "currency": currency,
                    "total": total,
                    "free": free,
                    "used": used,
                    "quote": quote,
                    "current_price": current_price,
                    "value_quote": value_quote,
                }
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ai_insights")
async def get_ai_insights() -> Dict:
    """AI 분석/신호 인사이트 (심볼별 최근 판단)"""
    bot = _require_bot()
    return getattr(bot, "_last_signal_insights", {})


@router.get("/stats")
async def get_stats() -> Dict:
    """통계 (손익률 포함)"""
    bot = _require_bot()
    stats = await bot.position_store.get_stats()
    stats["balance"] = bot.risk_manager.total_balance
    stats["initial_balance"] = bot.risk_manager.initial_balance
    # 미실현 손익 합계
    total_unrealized = sum(
        p.unrealized_pnl for p in bot.risk_manager.positions.values()
    )
    stats["total_unrealized_pnl"] = total_unrealized
    # 총 수익률 = (실현손익 + 미실현손익) / 초기잔고 * 100
    initial = bot.risk_manager.initial_balance or 1
    total_pnl = stats.get("total_pnl", 0) or 0
    stats["total_return_percent"] = (total_pnl + total_unrealized) / initial * 100
    return stats


def _build_strategy_descriptions(cfg: Dict) -> Dict[str, str]:
    """전략별 매매 기준 요약 문자열 생성"""
    indicators = cfg.get("indicators", {})
    strategies = cfg.get("strategies", {})

    desc: Dict[str, str] = {}

    # RSI
    rsi_cfg = indicators.get("rsi", {})
    desc["rsi"] = (
        f"RSI 기반 매매: RSI ≤ {rsi_cfg.get('oversold', 30)} 에서 매수, "
        f"RSI ≥ {rsi_cfg.get('overbought', 70)} 에서 매도"
    )

    # MACD
    macd_cfg = indicators.get("macd", {})
    desc["macd"] = (
        "MACD 기반 매매: MACD 선이 시그널 선을 상향 돌파(골든크로스)하면 매수, "
        "하향 돌파(데드크로스)하면 매도"
    )

    # 단타(Scalping)
    scalping_cfg = strategies.get("scalping", {})
    scal_rsi = scalping_cfg.get("rsi", {})
    scal_macd = scalping_cfg.get("macd", {})
    require_both = scalping_cfg.get("require_both", False)
    cond = "RSI + MACD 둘 다 매수 방향일 때만 진입" if require_both else "RSI 또는 MACD 중 하나만 매수 방향이어도 진입"
    desc["scalping"] = (
        "단타(Scalping) 전략: "
        f"빠른 RSI(period={scal_rsi.get('period', 7)}, oversold={scal_rsi.get('oversold', 35)}, "
        f"overbought={scal_rsi.get('overbought', 75)})와 "
        f"짧은 MACD(fast={scal_macd.get('fast_period', 6)}, slow={scal_macd.get('slow_period', 13)}, "
        f"signal={scal_macd.get('signal_period', 5)})를 함께 사용, {cond}"
    )

    # Combined (규칙 조합)
    desc["combined"] = "RSI + MACD + 볼린저밴드 신호를 조합해 매매 (강한 합의가 있을 때만 진입)"

    # AI 하이브리드 설명
    ai_cfg = cfg.get("ai", {})
    mode = ai_cfg.get("mode", "hybrid")
    if mode == "hybrid":
        desc["hybrid"] = (
            "AI 하이브리드: 기본 규칙 전략 신호를 우선 사용하고, Gemini AI가 신호를 보조 판단하여 "
            "신뢰도(confidence)에 따라 진입/보류를 결정"
        )
    elif mode == "primary":
        desc["gemini"] = "Gemini AI 단독 전략: 과거 가격·지표 요약을 기반으로 AI가 직접 매수/매도 신호를 생성"

    return desc


@router.get("/strategies")
async def get_strategies() -> Dict:
    """
    매수 전략 목록 및 현재 설정 조회
    - available: 선택 가능한 매수 전략
    - current: 현재 사용 중인 규칙/실제 전략
    - description: 각 전략별 매매 기준 요약
    """
    bot = _require_bot()
    cfg = bot.config
    strategies_cfg = cfg.get("strategies", {})
    enabled: List[str] = strategies_cfg.get("enabled", []) or []
    default_rule = strategies_cfg.get("default", "rsi")

    ai_cfg = cfg.get("ai", {})
    ai_enabled = bool(ai_cfg.get("enabled", False))
    ai_mode = ai_cfg.get("mode", "hybrid")

    # 실제로 SignalGenerator 가 사용하는 전략 이름
    effective = bot.signal_generator.strategy_name

    descriptions = _build_strategy_descriptions(cfg)

    available = []
    for name in enabled:
        available.append(
            {
                "name": name,
                "label": name.upper() if name != "scalping" else "SCALPING (단타)",
                "description": descriptions.get(name, ""),
            }
        )
    # combined 전략은 enabled 에 없더라도 선택지로 노출할 수 있음
    if "combined" not in [a["name"] for a in available]:
        available.append(
            {
                "name": "combined",
                "label": "COMBINED (RSI+MACD+BB)",
                "description": descriptions.get("combined", ""),
            }
        )

    # AI 관련 전략 설명 추가
    ai_strategies = {}
    if "hybrid" in descriptions:
        ai_strategies["hybrid"] = descriptions["hybrid"]
    if "gemini" in descriptions:
        ai_strategies["gemini"] = descriptions["gemini"]

    return {
        "available": available,
        "current": {
            "base": default_rule,
            "effective": effective,
            "ai_enabled": ai_enabled,
            "ai_mode": ai_mode,
        },
        "descriptions": descriptions,
        "ai_strategies": ai_strategies,
    }


@router.post("/strategy")
async def set_strategy(payload: Dict[str, Any]) -> Dict:
    """
    매수 전략 변경 (웹 UI에서 선택)
    - name: rsi, macd, scalping, combined 중 하나
    - AI 하이브리드가 켜져 있으면, 규칙 기본 전략만 변경하고 실제 전략은 hybrid 유지
    """
    bot = _require_bot()
    cfg = bot.config
    strategies_cfg = cfg.setdefault("strategies", {})
    enabled: List[str] = strategies_cfg.get("enabled", []) or []

    name = (payload or {}).get("name")
    if not name or name not in (enabled + ["combined"]):
        raise HTTPException(status_code=400, detail="지원하지 않는 전략입니다.")

    # 규칙 기본 전략 갱신
    strategies_cfg["default"] = name

    # AI 설정에 따라 실제 사용할 전략 결정
    ai_cfg = cfg.get("ai", {})
    ai_enabled = bool(ai_cfg.get("enabled", False))
    ai_mode = ai_cfg.get("mode", "hybrid")

    if ai_enabled:
        if ai_mode == "hybrid":
            # 하이브리드: 규칙 기본 전략만 바꾸고 strategy_name 은 hybrid 유지
            strategy_name = "hybrid"
        elif ai_mode == "primary":
            strategy_name = "gemini"
        else:
            strategy_name = name
    else:
        strategy_name = name

    bot.signal_generator = SignalGenerator(strategy_name=strategy_name, config=cfg)

    descriptions = _build_strategy_descriptions(cfg)
    return {
        "success": True,
        "current": {
            "base": name,
            "effective": bot.signal_generator.strategy_name,
            "ai_enabled": ai_enabled,
            "ai_mode": ai_mode,
        },
        "description": descriptions.get(name, ""),
    }


@router.post("/ai/toggle")
async def toggle_ai() -> Dict:
    """
    AI 활성/비활성 토글
    - config.ai.enabled 값을 반전
    - SignalGenerator 를 현재 설정에 맞게 재초기화
    """
    bot = _require_bot()
    cfg = bot.config
    ai_cfg = cfg.get("ai", {}) or {}
    current = bool(ai_cfg.get("enabled", False))
    new_value = not current
    ai_cfg["enabled"] = new_value
    cfg["ai"] = ai_cfg

    # 전략 이름 재선택 (TradingBot 초기화 로직과 동일하게)
    if new_value:
        strategy_name = ai_cfg.get("mode", "hybrid")
        if strategy_name == "primary":
            strategy_name = "gemini"
    else:
        strategy_name = cfg.get("strategies", {}).get("default", "rsi")

    bot.signal_generator = SignalGenerator(strategy_name=strategy_name, config=cfg)

    return {
        "success": True,
        "ai_enabled": new_value,
        "strategy": bot.signal_generator.strategy_name,
    }


@router.post("/stop")
async def request_stop() -> Dict:
    """봇 중지 요청"""
    bot = _require_bot()
    if not bot._running:
        return {"success": True, "message": "이미 중지됨"}
    bot.stop()
    return {"success": True, "message": "중지 요청됨"}
