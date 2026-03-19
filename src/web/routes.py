"""
API 라우트
봇 상태, 포지션, 가격, 설정 조회
"""
from typing import Dict, List, Optional, Any

from fastapi import APIRouter, HTTPException

from .state import get_bot


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
        for currency, b in balances.items():
            total = getattr(b, "total", 0) if hasattr(b, "total") else (b.get("total", 0) if isinstance(b, dict) else 0)
            if total > 0:
                free = getattr(b, "free", 0) if hasattr(b, "free") else (b.get("free", 0) if isinstance(b, dict) else 0)
                used = getattr(b, "used", 0) if hasattr(b, "used") else (b.get("used", 0) if isinstance(b, dict) else 0)
                result[currency] = {
                    "currency": currency,
                    "total": total,
                    "free": free,
                    "used": used,
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


@router.post("/stop")
async def request_stop() -> Dict:
    """봇 중지 요청"""
    bot = _require_bot()
    if not bot._running:
        return {"success": True, "message": "이미 중지됨"}
    bot.stop()
    return {"success": True, "message": "중지 요청됨"}
