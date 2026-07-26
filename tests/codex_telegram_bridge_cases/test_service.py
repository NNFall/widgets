from pathlib import Path

from tools.codex_telegram_bridge.config import BridgeConfig
from tools.codex_telegram_bridge.service import BridgeService
from tools.codex_telegram_bridge.store import BridgeStore


THREAD_ID = "019f9e1b-fb04-7482-b62d-cee4c051131b"
OTHER_THREAD_ID = "019e5496-6c7c-7bb2-af7e-479bcf07edd4"


class FakeTelegram:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []
        self.actions: list[tuple[int, str]] = []

    def send_message(self, chat_id: int, text: str) -> None:
        self.messages.append((chat_id, text))

    def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        self.actions.append((chat_id, action))


class FakeRunner:
    def __init__(self, answer: str = "Готовый ответ") -> None:
        self.answer = answer
        self.calls: list[tuple[str, str, str]] = []
        self.forgotten: list[str] = []

    def run(self, thread_id: str, prompt: str, *, request_id: str) -> str:
        self.calls.append((thread_id, prompt, request_id))
        return self.answer

    def forget(self, request_id: str) -> None:
        self.forgotten.append(request_id)


def _config(tmp_path: Path) -> BridgeConfig:
    return BridgeConfig(
        telegram_token="secret",
        allowed_user_ids=frozenset({10}),
        chat_bindings={20: THREAD_ID},
        data_dir=tmp_path,
        codex_home=tmp_path / ".codex",
        codex_command="codex.cmd",
        poll_timeout_seconds=30,
        turn_timeout_seconds=3600,
        retry_delay_seconds=1.0,
    )


def _update(update_id: int, text: str | None, *, user_id: int = 10, chat_id: int = 20) -> dict:
    message = {"from": {"id": user_id}, "chat": {"id": chat_id}}
    if text is not None:
        message["text"] = text
    else:
        message["photo"] = [{"file_id": "photo"}]
    return {"update_id": update_id, "message": message}


def test_unauthorized_user_cannot_enqueue_codex_turn(tmp_path: Path) -> None:
    telegram = FakeTelegram()
    runner = FakeRunner()
    store = BridgeStore(tmp_path / "db.sqlite3")
    service = BridgeService(_config(tmp_path), telegram, runner, store)

    service.handle_update(_update(1, "run something", user_id=999))

    assert store.pending_count() == 0
    assert runner.calls == []
    assert "999" in telegram.messages[-1][1]
    import sqlite3

    with sqlite3.connect(tmp_path / "db.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM processed_updates").fetchone()[0] == 0


def test_text_is_queued_once_and_delivered_from_same_thread(tmp_path: Path) -> None:
    telegram = FakeTelegram()
    runner = FakeRunner(answer="x" * 4500)
    store = BridgeStore(tmp_path / "db.sqlite3")
    service = BridgeService(_config(tmp_path), telegram, runner, store)

    service.handle_update(_update(1, "Исправь пост"))
    service.handle_update(_update(1, "Исправь пост"))
    assert store.pending_count() == 1

    assert service.process_one() is True

    assert runner.calls == [(THREAD_ID, "Исправь пост", "1")]
    assert runner.forgotten == ["1", "1"]
    response_chunks = [text for _, text in telegram.messages if text.startswith("x")]
    assert "".join(response_chunks) == "x" * 4500
    assert store.pending_count() == 0


def test_use_command_changes_thread_for_following_messages(tmp_path: Path) -> None:
    telegram = FakeTelegram()
    runner = FakeRunner()
    store = BridgeStore(tmp_path / "db.sqlite3")
    service = BridgeService(_config(tmp_path), telegram, runner, store)

    service.handle_update(_update(1, f"/use {OTHER_THREAD_ID}"))
    service.handle_update(_update(2, "new task message"))
    service.process_one()

    assert store.get_binding(20) == OTHER_THREAD_ID
    assert runner.calls == [(OTHER_THREAD_ID, "new task message", "1")]


def test_commands_report_status_and_media_limit(tmp_path: Path) -> None:
    telegram = FakeTelegram()
    store = BridgeStore(tmp_path / "db.sqlite3")
    service = BridgeService(_config(tmp_path), telegram, FakeRunner(), store)

    service.handle_update(_update(1, "/thread"))
    service.handle_update(_update(2, "/status"))
    service.handle_update(_update(3, None))

    texts = [text for _, text in telegram.messages]
    assert any(THREAD_ID in text for text in texts)
    assert any("очеред" in text.lower() for text in texts)
    assert any("только текст" in text.lower() for text in texts)


def test_partial_long_reply_resumes_after_last_delivered_chunk(tmp_path: Path) -> None:
    class FailSecondChunkOnce(FakeTelegram):
        def __init__(self) -> None:
            super().__init__()
            self.long_chunk_attempts = 0

        def send_message(self, chat_id: int, text: str) -> None:
            if text.startswith("x"):
                self.long_chunk_attempts += 1
                if self.long_chunk_attempts == 2:
                    from tools.codex_telegram_bridge.telegram_api import TelegramApiError

                    raise TelegramApiError("temporary")
            super().send_message(chat_id, text)

    telegram = FailSecondChunkOnce()
    runner = FakeRunner(answer="x" * 9000)
    store = BridgeStore(tmp_path / "db.sqlite3")
    service = BridgeService(_config(tmp_path), telegram, runner, store)
    service.handle_update(_update(1, "long answer"))

    import pytest
    from tools.codex_telegram_bridge.telegram_api import TelegramApiError

    with pytest.raises(TelegramApiError):
        service.process_one()
    assert store.next_delivery().delivery_cursor == 1

    assert service.process_one() is True
    delivered = [text for _, text in telegram.messages if text.startswith("x")]
    assert "".join(delivered) == "x" * 9000


def test_maintenance_prunes_hourly_during_long_running_service(tmp_path: Path) -> None:
    store = BridgeStore(tmp_path / "db.sqlite3")
    service = BridgeService(_config(tmp_path), FakeTelegram(), FakeRunner(), store)
    calls: list[int] = []
    original = store.prune_history

    def tracked(retention_days: int) -> tuple[int, int]:
        calls.append(retention_days)
        return original(retention_days)

    store.prune_history = tracked

    service.run_maintenance(now=100.0)
    service.run_maintenance(now=200.0)
    service.run_maintenance(now=3700.0)

    assert calls == [30, 30]
