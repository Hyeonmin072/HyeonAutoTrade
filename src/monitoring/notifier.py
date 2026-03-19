"""
알림 모듈
Telegram, Slack 등 다양한 채널로 알림 전송
"""
import asyncio
from enum import Enum
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from abc import ABC, abstractmethod

import httpx

from .logger import get_logger


logger = get_logger("notifier")


class NotificationLevel(Enum):
    """알림 레벨"""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class Notification:
    """알림 메시지"""
    level: NotificationLevel
    title: str
    message: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    symbol: Optional[str] = None  # 거래 심볼 (관련 있는 경우)
    
    def to_dict(self) -> Dict:
        return {
            "level": self.level.value,
            "title": self.title,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
            "symbol": self.symbol
        }
    
    def __str__(self) -> str:
        return f"[{self.level.value.upper()}] {self.title}: {self.message}"


class NotificationChannel(ABC):
    """알림 채널 기본 클래스"""
    
    @abstractmethod
    async def send(self, notification: Notification) -> bool:
        """알림 전송 (성공 시 True 반환)"""
        pass
    
    @abstractmethod
    async def test(self) -> bool:
        """연결 테스트"""
        pass


class TelegramNotifier(NotificationChannel):
    """Telegram 알림 채널"""
    
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        enabled: bool = True,
        timeout: int = 30
    ):
        """
        Args:
            bot_token: Telegram Bot Token
            chat_id: Telegram Chat ID
            enabled: 활성화 여부
            timeout: 요청 타임아웃 (초)
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = enabled
        self.timeout = timeout
        self.api_url = f"https://api.telegram.org/bot{bot_token}"
        
        # 이모지 매핑
        self._emojis = {
            NotificationLevel.DEBUG: "🔍",
            NotificationLevel.INFO: "ℹ️",
            NotificationLevel.WARNING: "⚠️",
            NotificationLevel.ERROR: "❌",
            NotificationLevel.CRITICAL: "🚨"
        }
    
    async def send(self, notification: Notification) -> bool:
        """Telegram로 알림 전송"""
        if not self.enabled:
            return False
        
        emoji = self._emojis.get(notification.level, "📢")
        
        # 메시지 포맷팅
        text = f"""
{emoji} *{notification.title}*

_{notification.message}_

⏰ {notification.timestamp.strftime('%Y-%m-%d %H:%M:%S')}
"""
        
        # 메타데이터 추가
        if notification.metadata:
            text += "\n📊 상세:\n"
            for key, value in notification.metadata.items():
                text += f"  • `{key}`: `{value}`\n"
        
        if notification.symbol:
            text += f"\n💰 심볼: `{notification.symbol}`"
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.api_url}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": text,
                        "parse_mode": "Markdown"
                    }
                )
                response.raise_for_status()
                logger.debug(f"Telegram notification sent: {notification.title}")
                return True
                
        except httpx.HTTPStatusError as e:
            logger.error(f"Telegram HTTP error: {e.response.status_code}")
            return False
        except Exception as e:
            logger.error(f"Telegram send error: {e}")
            return False
    
    async def test(self) -> bool:
        """연결 테스트"""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(f"{self.api_url}/getMe")
                response.raise_for_status()
                data = response.json()
                return data.get("ok", False)
        except Exception as e:
            logger.error(f"Telegram test failed: {e}")
            return False


class SlackNotifier(NotificationChannel):
    """Slack 알림 채널"""
    
    def __init__(
        self,
        webhook_url: str,
        enabled: bool = True,
        timeout: int = 30
    ):
        """
        Args:
            webhook_url: Slack Webhook URL
            enabled: 활성화 여부
            timeout: 요청 타임아웃 (초)
        """
        self.webhook_url = webhook_url
        self.enabled = enabled
        self.timeout = timeout
        
        # 색상 매핑
        self._colors = {
            NotificationLevel.DEBUG: "#808080",
            NotificationLevel.INFO: "#36a64f",
            NotificationLevel.WARNING: "#ff9800",
            NotificationLevel.ERROR: "#f44336",
            NotificationLevel.CRITICAL: "#b71c1c"
        }
    
    async def send(self, notification: Notification) -> bool:
        """Slack로 알림 전송"""
        if not self.enabled:
            return False
        
        payload = {
            "attachments": [{
                "color": self._colors.get(notification.level, "#808080"),
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": f"🚨 {notification.title}" if notification.level == NotificationLevel.CRITICAL else notification.title
                        }
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": notification.message
                        }
                    },
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": f"🕐 {notification.timestamp.strftime('%Y-%m-%d %H:%M:%S')} | *{notification.level.value.upper()}*"
                            }
                        ]
                    }
                ]
            }]
        }
        
        # 메타데이터 추가
        if notification.metadata:
            fields = []
            for key, value in list(notification.metadata.items())[:5]:
                fields.append({
                    "type": "mrkdwn",
                    "text": f"*{key}*\n{value}"
                })
            
            payload["attachments"][0]["blocks"].append({
                "type": "section",
                "fields": fields
            })
        
        if notification.symbol:
            payload["attachments"][0]["blocks"].append({
                "type": "context",
                "elements": [{
                    "type": "mrkdwn",
                    "text": f"💰 심볼: `{notification.symbol}`"
                }]
            })
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(self.webhook_url, json=payload)
                response.raise_for_status()
                logger.debug(f"Slack notification sent: {notification.title}")
                return True
                
        except httpx.HTTPStatusError as e:
            logger.error(f"Slack HTTP error: {e.response.status_code}")
            return False
        except Exception as e:
            logger.error(f"Slack send error: {e}")
            return False
    
    async def test(self) -> bool:
        """연결 테스트"""
        test_notification = Notification(
            level=NotificationLevel.INFO,
            title="테스트 알림",
            message="AutoCoinTrade 알림 시스템이 정상적으로 연결되었습니다."
        )
        return await self.send(test_notification)


class NotifierManager:
    """
    알림 관리자
    여러 채널을 통해 알림을 동시 전송
    """
    
    def __init__(self):
        self._channels: List[NotificationChannel] = []
        self._min_level = NotificationLevel.INFO
        self._rate_limiter: Dict[str, List[datetime]] = {}  # 채널별 마지막 전송 시간
        self._rate_limit_seconds = 60  # 동일 알림 최소 간격
    
    def add_channel(self, channel: NotificationChannel) -> None:
        """알림 채널 추가"""
        self._channels.append(channel)
        logger.info(f"Added notification channel: {channel.__class__.__name__}")
    
    def set_min_level(self, level: NotificationLevel) -> None:
        """최소 알림 레벨 설정"""
        self._min_level = level
    
    def _should_send(self, channel: NotificationChannel, notification: Notification) -> bool:
        """전송 필요 여부 (레이트 리밋)"""
        channel_name = channel.__class__.__name__
        current_time = datetime.now()
        
        if channel_name not in self._rate_limiter:
            self._rate_limiter[channel_name] = []
        
        # 마지막 전송 시간 확인
        last_times = self._rate_limiter[channel_name]
        if last_times:
            time_since_last = (current_time - last_times[-1]).total_seconds()
            if time_since_last < self._rate_limit_seconds:
                return False
        
        # 오래된 기록 정리
        cutoff = current_time - timedelta(seconds=self._rate_limit_seconds * 2)
        self._rate_limiter[channel_name] = [
            t for t in last_times if t > cutoff
        ]
        
        return True
    
    async def send(
        self,
        level: NotificationLevel,
        title: str,
        message: str,
        symbol: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> None:
        """
        모든 채널로 알림 전송
        
        Args:
            level: 알림 레벨
            title: 제목
            message: 메시지
            symbol: 거래 심볼
            metadata: 추가 메타데이터
        """
        # 레벨 필터링
        if level.value < self._min_level.value:
            return
        
        notification = Notification(
            level=level,
            title=title,
            message=message,
            symbol=symbol,
            metadata=metadata or {}
        )
        
        # 모든 채널에 동시 전송
        tasks = []
        for channel in self._channels:
            if self._should_send(channel, notification):
                tasks.append(self._send_with_log(channel, notification))
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _send_with_log(
        self,
        channel: NotificationChannel,
        notification: Notification
    ) -> None:
        """채널로 전송 및 로그"""
        try:
            success = await channel.send(notification)
            if success:
                channel_name = channel.__class__.__name__
                logger.debug(f"Notification sent via {channel_name}")
            else:
                logger.warning(f"Failed to send notification via {channel.__class__.__name__}")
        except Exception as e:
            logger.error(f"Notification error: {e}")
    
    # 편의 메서드들
    async def info(self, title: str, message: str, **kwargs) -> None:
        await self.send(NotificationLevel.INFO, title, message, **kwargs)
    
    async def warning(self, title: str, message: str, **kwargs) -> None:
        await self.send(NotificationLevel.WARNING, title, message, **kwargs)
    
    async def error(self, title: str, message: str, **kwargs) -> None:
        await self.send(NotificationLevel.ERROR, title, message, **kwargs)
    
    async def critical(self, title: str, message: str, **kwargs) -> None:
        await self.send(NotificationLevel.CRITICAL, title, message, **kwargs)
    
    # 거래 관련 편의 메서드
    async def notify_trade(
        self,
        action: str,  # "BUY" or "SELL"
        symbol: str,
        price: float,
        amount: float,
        **kwargs
    ) -> None:
        """거래 알림"""
        action_ko = {
            "BUY": "매수",
            "SELL": "매도",
            "SHORT": "숏 진입",
            "CLOSE_SHORT": "숏 청산",
        }.get(action, action)
        await self.send(
            NotificationLevel.INFO,
            f"거래 체결: {action_ko}",
            f"{action_ko} {amount} {symbol} @ {price:,.0f}",
            symbol=symbol,
            metadata={
                "거래": action_ko,
                "가격": str(price),
                "수량": str(amount)
            }
        )
    
    async def notify_signal(
        self,
        signal: str,  # "BUY", "SELL", "HOLD"
        symbol: str,
        reason: str,
        **kwargs
    ) -> None:
        """신호 알림"""
        level = {
            "BUY": NotificationLevel.INFO,
            "SELL": NotificationLevel.WARNING,
            "HOLD": NotificationLevel.DEBUG
        }.get(signal, NotificationLevel.INFO)
        
        signal_ko = {"BUY": "매수", "SELL": "매도", "HOLD": "보유"}.get(signal, signal)
        await self.send(
            level,
            f"신호: {signal_ko}",
            f"{symbol} {signal_ko} 신호: {reason}",
            symbol=symbol
        )
    
    async def notify_error(
        self,
        error_type: str,
        message: str,
        **kwargs
    ) -> None:
        """오류 알림"""
        await self.send(
            NotificationLevel.ERROR,
            f"오류: {error_type}",
            message,
            metadata=kwargs.get("metadata", {})
        )
    
    async def notify_startup(self, mode: str = "production") -> None:
        """시작 알림"""
        mode_ko = "시뮬레이션" if mode == "simulation" else "실거래"
        await self.info(
            "AutoCoinTrade 시작",
            f"트레이딩 봇이 {mode_ko} 모드로 시작되었습니다.",
            metadata={"mode": mode}
        )
    
    async def notify_shutdown(self) -> None:
        """종료 알림"""
        await self.info(
            "AutoCoinTrade 종료",
            "트레이딩 봇이 정상적으로 종료되었습니다."
        )
    
    async def test_all_channels(self) -> Dict[str, bool]:
        """모든 채널 연결 테스트"""
        results = {}
        for channel in self._channels:
            results[channel.__class__.__name__] = await channel.test()
        return results


# NotifierManager의 별칭 (하위 호환성)
Notifier = NotifierManager
