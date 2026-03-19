"""
AutoCoinTrade 웹 UI
로컬 전용 대시보드 - 상태 확인 및 모니터링
"""
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from .routes import router as api_router
from .state import get_bot, set_bot


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 생명주기 - 봇 백그라운드 실행"""
    import asyncio
    bot_task = None
    bot = get_bot()
    if bot:
        bot_task = asyncio.create_task(bot.start())
    yield
    # 종료 시 봇 정리
    if bot:
        bot.stop()
    if bot_task and not bot_task.done():
        try:
            await asyncio.wait_for(bot_task, timeout=15)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            bot_task.cancel()


def create_app() -> FastAPI:
    """FastAPI 앱 생성"""
    app = FastAPI(
        title="AutoCoinTrade",
        description="자동 코인 트레이딩 봇 대시보드",
        version="1.0.0",
        lifespan=lifespan,
    )

    # API 라우트
    app.include_router(api_router, prefix="/api", tags=["api"])

    # 정적 파일 (static 폴더)
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        """메인 페이지"""
        html_path = static_dir / "index.html"
        if html_path.exists():
            return FileResponse(html_path)
        return HTMLResponse("<h1>AutoCoinTrade</h1><p>static/index.html을 확인하세요.</p>")

    return app
