"""
웹 UI와 함께 봇 실행
로컬 전용: http://127.0.0.1:8080
"""
import argparse
import sys
from pathlib import Path

# 프로젝트 루트
_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.main import TradingBot
from src.web.app import create_app
from src.web.state import set_bot
from src.monitoring.logger import get_logger

logger = get_logger("web")


def main():
    parser = argparse.ArgumentParser(description="AutoCoinTrade 웹 UI")
    parser.add_argument("--host", default="127.0.0.1", help="바인드 주소 (기본: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="포트 (기본: 8080)")
    parser.add_argument("--config", default="config/config.yaml", help="설정 파일 경로")
    args = parser.parse_args()

    # 봇 생성 및 주입
    bot = TradingBot(config_path=args.config)
    set_bot(bot)

    app = create_app()

    # uvicorn 실행
    import uvicorn

    def run():
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level="info",
        )

    logger.info(f"웹 UI 시작: http://{args.host}:{args.port}")
    run()


if __name__ == "__main__":
    main()
