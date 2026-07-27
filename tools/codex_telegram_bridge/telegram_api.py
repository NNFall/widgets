from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable


class TelegramApiError(RuntimeError):
    """A sanitized Telegram Bot API failure."""


UrlOpener = Callable[..., object]


def split_message(text: str, *, limit: int = 4000) -> list[str]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    if not text:
        return []
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        boundary = remaining.rfind("\n", 0, limit + 1)
        if boundary <= 0:
            boundary = remaining.rfind(" ", 0, limit + 1)
        if boundary <= 0:
            boundary = limit
        else:
            boundary += 1
        chunks.append(remaining[:boundary])
        remaining = remaining[boundary:]
    if remaining:
        chunks.append(remaining)
    return chunks


class TelegramClient:
    def __init__(
        self,
        token: str,
        *,
        api_base: str = "https://api.telegram.org",
        opener: UrlOpener = urllib.request.urlopen,
    ) -> None:
        self._base_url = f"{api_base.rstrip('/')}/bot{token}"
        self._opener = opener

    def _call(self, method: str, payload: dict[str, object], *, timeout: float = 35) -> object:
        request = urllib.request.Request(
            f"{self._base_url}/{method}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with self._opener(request, timeout=timeout) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", None)
            detail = str(reason) if reason else exc.__class__.__name__
            raise TelegramApiError(f"Telegram API is unavailable: {detail}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TelegramApiError("Telegram API returned an invalid response") from exc
        if not isinstance(decoded, dict) or decoded.get("ok") is not True:
            description = decoded.get("description", "unknown Telegram API error") if isinstance(decoded, dict) else "invalid response"
            raise TelegramApiError(f"Telegram API error: {description}")
        return decoded.get("result")

    def get_updates(self, *, offset: int | None, timeout_seconds: int) -> list[dict]:
        payload: dict[str, object] = {
            "timeout": timeout_seconds,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = self._call("getUpdates", payload, timeout=timeout_seconds + 5)
        return result if isinstance(result, list) else []

    def send_message(self, chat_id: int, text: str) -> None:
        if not text or len(text) > 4096:
            raise ValueError("Telegram message must contain 1 to 4096 characters")
        self._call("sendMessage", {"chat_id": chat_id, "text": text})

    def copy_message(self, chat_id: int, from_chat_id: str, message_id: int) -> None:
        self._call(
            "copyMessage",
            {
                "chat_id": chat_id,
                "from_chat_id": from_chat_id,
                "message_id": message_id,
            },
        )

    def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        self._call("sendChatAction", {"chat_id": chat_id, "action": action})
