"""봇 인스턴스 상태 (순환 import 방지)"""
from typing import Optional, Any

_bot_instance: Optional[Any] = None


def set_bot(bot: Any) -> None:
    """봇 인스턴스 설정"""
    global _bot_instance
    _bot_instance = bot


def get_bot() -> Optional[Any]:
    """봇 인스턴스 반환"""
    return _bot_instance
