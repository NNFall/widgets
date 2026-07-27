# Windows Telegram ↔ Codex Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Создать переиспользуемый Windows-мост, который передаёт разрешённые Telegram-сообщения как новые ходы в выбранные задачи Codex.

**Architecture:** Автономный Python-процесс получает Telegram Update через long polling, надёжно сохраняет их в SQLite и по одному вызывает `codex.cmd exec resume`. Секреты и рабочие данные живут вне Git в `%LOCALAPPDATA%` и Windows environment.

**Tech Stack:** Python 3.11+, standard library (`urllib`, `sqlite3`, `subprocess`, `logging`), PowerShell 5.1+, Windows Task Scheduler, pytest.

---

### Task 1: Конфигурация и валидация

**Files:**
- Create: `tools/codex_telegram_bridge/__init__.py`
- Create: `tools/codex_telegram_bridge/config.py`
- Create: `tools/codex_telegram_bridge/config.example.json`
- Test: `tests/codex_telegram_bridge_cases/test_config.py`

- [x] Написать тесты на валидный config, отсутствующий token, неверный UUID и Telegram ID.
- [x] Запустить `python -m pytest tests/codex_telegram_bridge_cases/test_config.py -q` и увидеть ожидаемое падение импорта.
- [x] Реализовать immutable `BridgeConfig`, раскрытие `%VAR%`, defaults и проверку.
- [x] Повторить тест до зелёного статуса.

### Task 2: Надёжная SQLite-очередь

**Files:**
- Create: `tools/codex_telegram_bridge/store.py`
- Test: `tests/codex_telegram_bridge_cases/test_store.py`

- [x] Написать тесты на deduplication, FIFO, привязку чата, delivery cursor,
  очистку приватного содержимого и recovery `running → pending`.
- [x] Запустить `python -m pytest tests/codex_telegram_bridge_cases/test_store.py -q` и увидеть ожидаемое падение.
- [x] Реализовать schema, атомарные транзакции и методы job lifecycle.
- [x] Повторить тест до зелёного статуса.

### Task 3: Безопасный запуск Codex CLI

**Files:**
- Create: `tools/codex_telegram_bridge/codex_runner.py`
- Test: `tests/codex_telegram_bridge_cases/test_codex_runner.py`

- [x] Написать тесты на массив аргументов, stdin, `CODEX_HOME`, durable output,
  повторное использование ответа, завершение дерева процессов, timeout и non-zero exit.
- [x] Запустить `python -m pytest tests/codex_telegram_bridge_cases/test_codex_runner.py -q` и увидеть ожидаемое падение.
- [x] Реализовать `CodexRunner` без `shell=True`, с устойчивым outbox-файлом до
  подтверждённой записи ответа в SQLite.
- [x] Повторить тест до зелёного статуса.

### Task 4: Telegram API и разбиение ответов

**Files:**
- Create: `tools/codex_telegram_bridge/telegram_api.py`
- Test: `tests/codex_telegram_bridge_cases/test_telegram_api.py`

- [x] Написать тесты JSON HTTP contract и `split_message`, сохраняющего весь текст.
- [x] Запустить `python -m pytest tests/codex_telegram_bridge_cases/test_telegram_api.py -q` и увидеть ожидаемое падение.
- [x] Реализовать HTTPS-клиент с timeout и понятными ошибками, не включающими token.
- [x] Повторить тест до зелёного статуса.

### Task 5: Сервис, команды и worker

**Files:**
- Create: `tools/codex_telegram_bridge/service.py`
- Create: `tools/codex_telegram_bridge/__main__.py`
- Create: `tools/codex_telegram_bridge/instance_lock.py`
- Test: `tests/codex_telegram_bridge_cases/test_service.py`

- [x] Написать тесты на whitelist, `/start`, `/status`, `/thread`, `/use`, plain text,
  unsupported media, single-instance lock, частичную доставку и job completion.
- [x] Запустить `python -m pytest tests/codex_telegram_bridge_cases/test_service.py -q` и увидеть ожидаемое падение.
- [x] Реализовать чистую обработку Update и отдельный polling/worker loop.
- [x] Повторить тест до зелёного статуса.

### Task 6: Windows-установка и документация

**Files:**
- Create: `tools/codex_telegram_bridge/README.md`
- Create: `tools/codex_telegram_bridge/setup.ps1`
- Create: `tools/codex_telegram_bridge/run.ps1`
- Create: `tools/codex_telegram_bridge/install-startup-task.ps1`
- Create: `tools/codex_telegram_bridge/uninstall-startup-task.ps1`
- Modify: `.gitignore`

- [x] Написать русскую инструкцию: BotFather, token, Telegram ID, thread UUID, ручной запуск, автозапуск и диагностика.
- [x] Создать setup script, который не печатает token и не пишет его в Git.
- [x] Создать idempotent install/uninstall Scheduled Task scripts без прав администратора.
- [x] Проверить PowerShell parser для всех `.ps1` без их запуска.

### Task 7: Общая проверка и редакторский контур

**Files:**
- Modify: `docs/product-journal/2026-07.md`
- Create: `docs/telegram/release-packets/2026-07-26-telegram-codex-bridge.md`
- Modify: `docs/telegram/content-backlog.md`

- [x] Запустить `python -m pytest tests/codex_telegram_bridge_cases -q`.
- [x] Запустить `python -m compileall -q tools/codex_telegram_bridge`.
- [x] Проверить `codex.cmd exec resume --help` и совпадение аргументов с `CodexRunner`.
- [x] Запустить `git diff --check` и поиск секретов в новых файлах.
- [x] Обновить журнал, release packet и backlog, отделив факт готовности кода от непройденной живой Telegram-проверки.
- [x] Создать локальный Git-коммит без push и без публикации в Telegram.
