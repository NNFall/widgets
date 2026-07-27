# Telegram Previous Post Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить в локальный Telegram-мост команду `/previous`, которая копирует последний опубликованный пост Kaigo в личный чат вместе с фотографиями и форматированием.

**Architecture:** Новый `PublishedPostHistory` читает редакционный JSONL и возвращает один или несколько Telegram message ID. `BridgeService` передаёт их в `TelegramClient.copy_message`; сам Telegram сохраняет тип вложения и подпись. Канал и путь к реестру задаются в конфигурации Windows.

**Tech Stack:** Python 3.11+, stdlib JSON/Path, Telegram Bot API, pytest, Windows Scheduled Task.

---

### Task 1: Чтение истории публикаций

**Files:**
- Create: `tools/codex_telegram_bridge/post_history.py`
- Create: `tests/codex_telegram_bridge_cases/test_post_history.py`

- [x] **Step 1: Write the failing tests**

Проверить одиночный `telegram_message_id`, массив `telegram_message_ids`, пропуск повреждённой JSONL-строки и пустой файл.

```python
history = PublishedPostHistory(path)
assert history.latest_message_ids() == (4,)
```

- [x] **Step 2: Run tests to verify RED**

Run: `python -m pytest tests/codex_telegram_bridge_cases/test_post_history.py -q`

Expected: FAIL because `post_history` does not exist.

- [x] **Step 3: Implement the reader**

```python
class PublishedPostHistory:
    def __init__(self, path: Path) -> None:
        self.path = path

    def latest_message_ids(self) -> tuple[int, ...] | None:
        latest = None
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or record.get("record_type") != "post":
                continue
            raw_ids = record.get("telegram_message_ids")
            if raw_ids is None:
                raw_ids = [record.get("telegram_message_id")]
            if isinstance(raw_ids, list) and raw_ids and all(
                isinstance(value, int) and value > 0 for value in raw_ids
            ):
                latest = tuple(raw_ids)
        return latest
```

- [x] **Step 4: Run tests to verify GREEN**

Run: `python -m pytest tests/codex_telegram_bridge_cases/test_post_history.py -q`

Expected: all tests pass.

### Task 2: Telegram API and service command

**Files:**
- Modify: `tools/codex_telegram_bridge/telegram_api.py`
- Modify: `tools/codex_telegram_bridge/service.py`
- Modify: `tests/codex_telegram_bridge_cases/test_telegram_api.py`
- Modify: `tests/codex_telegram_bridge_cases/test_service.py`

- [x] **Step 1: Write failing API and service tests**

```python
client.copy_message(20, "@kaigoww", 4)
assert captured["body"] == {
    "chat_id": 20,
    "from_chat_id": "@kaigoww",
    "message_id": 4,
}
```

Service tests require `/previous` and `/lastpost` to copy every returned ID in order, avoid `runner.run`, and show a Russian fallback when no post exists.

- [x] **Step 2: Run focused tests to verify RED**

Run: `python -m pytest tests/codex_telegram_bridge_cases/test_telegram_api.py tests/codex_telegram_bridge_cases/test_service.py -q`

Expected: FAIL because `copy_message` and the commands do not exist.

- [x] **Step 3: Add minimal protocol and command implementation**

```python
def copy_message(self, chat_id: int, from_chat_id: str, message_id: int) -> None:
    self._call("copyMessage", {
        "chat_id": chat_id,
        "from_chat_id": from_chat_id,
        "message_id": message_id,
    })
```

`BridgeService._handle_command` reads `post_history.latest_message_ids()`, copies each message and catches `TelegramApiError` with a user-facing fallback.

- [x] **Step 4: Run focused tests to verify GREEN**

Run: `python -m pytest tests/codex_telegram_bridge_cases/test_telegram_api.py tests/codex_telegram_bridge_cases/test_service.py -q`

Expected: all focused tests pass.

### Task 3: Configuration, deployment and documentation

**Files:**
- Modify: `tools/codex_telegram_bridge/config.py`
- Modify: `tools/codex_telegram_bridge/config.example.json`
- Modify: `tools/codex_telegram_bridge/__main__.py`
- Modify: `tools/codex_telegram_bridge/README.md`
- Modify: `tests/codex_telegram_bridge_cases/test_config.py`
- Modify: `%LOCALAPPDATA%\KaigoCodexTelegramBridge\config.json`

- [x] **Step 1: Write failing config tests**

```python
assert config.publication_channel == "@kaigoww"
assert config.published_registry_path == tmp_path / "published.jsonl"
```

- [x] **Step 2: Run config tests to verify RED**

Run: `python -m pytest tests/codex_telegram_bridge_cases/test_config.py -q`

Expected: FAIL because the fields do not exist.

- [x] **Step 3: Wire configuration and runtime**

Add required `publication_channel` and `published_registry_path`, construct `PublishedPostHistory` in `__main__.py`, and pass it into `BridgeService`.

- [x] **Step 4: Run the complete local verification**

Run:

```powershell
python -m pytest tests\codex_telegram_bridge_cases -q
python -m compileall -q tools\codex_telegram_bridge
git diff --check
```

Expected: zero failures and zero syntax errors.

- [x] **Step 5: Deploy locally and smoke-test**

Update the non-secret config, restart `Kaigo Codex Telegram Bridge`, send `/previous` from the allowed owner chat, and verify that message `4` is copied without changing `@kaigoww`.

- [x] **Step 6: Commit and push**

```powershell
git add tools/codex_telegram_bridge tests/codex_telegram_bridge_cases docs/superpowers/plans/2026-07-27-telegram-previous-post-command.md
git commit -m "feat: add Telegram previous post command"
git push origin codex/gemini-technical-foundation
```
