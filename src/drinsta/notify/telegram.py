"""게시 결과 알림용 최소 Telegram 클라이언트.

이 채널은 검수 없이 자동 게시하므로 버튼/폴링은 없고 메시지 전송만 한다. 봇은 AInstagram과
같은 봇/채팅을 써도 되고, 토큰이 없으면 알림 없이 조용히 넘어간다.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional, Protocol

logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4096


class Notifier(Protocol):
    def send(self, text: str) -> None: ...


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, prefix: str = "", http: Any = None):
        if http is None:
            import requests as http  # type: ignore[no-redef]
        self._http = http
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self.chat_id = chat_id
        self.prefix = prefix

    def send(self, text: str) -> None:
        message = f"{self.prefix}\n{text}" if self.prefix else text
        try:
            response = self._http.post(
                self._url,
                data={"chat_id": self.chat_id, "text": message[:TELEGRAM_MESSAGE_LIMIT]},
                timeout=30,
            )
            response.raise_for_status()
        except Exception as e:  # noqa: BLE001 - 알림 실패가 게시를 막으면 안 된다
            # requests 에러 메시지에는 봇 토큰이 담긴 URL이 들어가므로 타입만 남긴다
            logger.warning("텔레그램 알림 전송 실패: %s", type(e).__name__)

    @classmethod
    def from_env(cls, prefix: str = "") -> Optional["TelegramNotifier"]:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            return None
        return cls(token, chat_id, prefix=prefix)
