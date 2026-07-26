import json

import pytest

from tools.codex_telegram_bridge.telegram_api import TelegramApiError, TelegramClient, split_message


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_get_updates_sends_long_poll_contract() -> None:
    captured: dict[str, object] = {}

    def opener(request: object, timeout: float) -> FakeResponse:
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        captured["timeout"] = timeout
        return FakeResponse({"ok": True, "result": [{"update_id": 5}]})

    client = TelegramClient("token-value", opener=opener)
    result = client.get_updates(offset=5, timeout_seconds=30)

    assert result == [{"update_id": 5}]
    assert captured["url"].endswith("/bottoken-value/getUpdates")
    assert captured["body"] == {
        "offset": 5,
        "timeout": 30,
        "allowed_updates": ["message"],
    }
    assert captured["timeout"] == 35


def test_api_error_does_not_include_bot_token() -> None:
    def opener(request: object, timeout: float) -> FakeResponse:
        return FakeResponse({"ok": False, "description": "Bad Request"})

    client = TelegramClient("secret-token", opener=opener)

    with pytest.raises(TelegramApiError) as caught:
        client.send_message(123, "hello")

    assert "secret-token" not in str(caught.value)
    assert "Bad Request" in str(caught.value)


def test_copy_message_preserves_telegram_source_contract() -> None:
    captured: dict[str, object] = {}

    def opener(request: object, timeout: float) -> FakeResponse:
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        captured["timeout"] = timeout
        return FakeResponse({"ok": True, "result": {"message_id": 50}})

    client = TelegramClient("token-value", opener=opener)
    client.copy_message(20, "@kaigoww", 4)

    assert captured["url"].endswith("/bottoken-value/copyMessage")
    assert captured["body"] == {
        "chat_id": 20,
        "from_chat_id": "@kaigoww",
        "message_id": 4,
    }
    assert captured["timeout"] == 35


def test_split_message_preserves_every_character() -> None:
    text = (("a" * 3980) + "\n") * 3 + "tail"
    chunks = split_message(text, limit=4000)

    assert len(chunks) == 3
    assert all(0 < len(chunk) <= 4000 for chunk in chunks)
    assert "".join(chunks) == text
