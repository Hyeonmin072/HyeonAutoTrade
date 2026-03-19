#!/usr/bin/env python3
"""
Telegram 알림 테스트 스크립트
봇 실행 없이 Telegram 연결 및 알림 전송을 테스트합니다.

사용법:
    python test_telegram.py

필요 설정:
    - config/.env 또는 .env에 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    - 또는 config/config.yaml의 monitoring.notifications.telegram
"""
import asyncio
import os
import sys
from pathlib import Path

# 프로젝트 루트를 경로에 추가
_project_root = Path(__file__).resolve().parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from dotenv import load_dotenv

# 환경변수 로드
load_dotenv(_project_root / "config" / ".env")
load_dotenv(_project_root / ".env")

from src.monitoring.notifier import TelegramNotifier, Notifier


async def main() -> None:
    """Telegram 연결 테스트 및 테스트 알림 전송"""
    # 토큰/채팅ID: env 우선, config는 별도 로드 시 사용
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        # config.yaml에서 시도
        try:
            import yaml
            config_path = _project_root / "config" / "config.yaml"
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f)
                tg = cfg.get("monitoring", {}).get("notifications", {}).get("telegram", {})
                token = token or tg.get("bot_token", "").strip()
                chat_id = chat_id or tg.get("chat_id", "").strip()
        except Exception:
            pass

    if not token or not chat_id:
        print("[ERROR] Telegram 설정이 없습니다.")
        print("   config/.env 또는 .env에 다음을 설정하세요:")
        print("   TELEGRAM_BOT_TOKEN=your_bot_token")
        print("   TELEGRAM_CHAT_ID=your_chat_id")
        print("   또는 config/config.yaml의 monitoring.notifications.telegram")
        sys.exit(1)

    print("[*] Telegram 연결 테스트 중...")
    notifier = Notifier()
    notifier.add_channel(TelegramNotifier(bot_token=token, chat_id=chat_id))

    # 연결 테스트 (getMe API)
    results = await notifier.test_all_channels()
    for name, ok in results.items():
        status = "OK" if ok else "FAIL"
        print(f"   {name}: {status}")

    if not all(results.values()):
        print("\n[ERROR] 연결 실패. Bot Token과 Chat ID를 확인하세요.")
        sys.exit(1)

    # 테스트 알림 전송
    print("\n[*] 테스트 알림 전송 중...")
    await notifier.notify_trade("BUY", "KRW-BTC", 95000000, 0.001)

    print("[OK] 테스트 알림 전송 완료. Telegram 앱에서 확인하세요.")


if __name__ == "__main__":
    asyncio.run(main())
