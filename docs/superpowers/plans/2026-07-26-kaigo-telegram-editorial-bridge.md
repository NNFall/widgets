# Kaigo Telegram Editorial Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Создать устойчивый файловый мост между технической задачей Kaigo и отдельной задачей Telegram-редактора.

**Architecture:** Техническая задача записывает проверенные результаты в продуктовый журнал и release packets. Редакторская задача читает единый стартовый файл, использует очередь тем, доказательства и шаблоны, после чего сохраняет черновики и реестр публикаций.

**Tech Stack:** Markdown, JSON Lines, Git.

---

### Task 1: Создать продуктовый журнал

**Files:**
- Create: `docs/product-journal/README.md`
- Create: `docs/product-journal/2026-07.md`

- [x] Описать критерии значимого изменения и шаблон записи.
- [x] Заполнить июльский журнал подтверждённой историей проекта.

### Task 2: Создать рабочее пространство Telegram-редактора

**Files:**
- Create: `docs/telegram/START_HERE.md`
- Create: `docs/telegram/editorial-guide.md`
- Create: `docs/telegram/content-backlog.md`
- Create: `docs/telegram/templates/release-packet.md`
- Create: `docs/telegram/templates/post-draft.md`
- Create: `docs/telegram/drafts/README.md`
- Create: `docs/telegram/assets/README.md`
- Create: `docs/telegram/published.jsonl`

- [x] Создать единую точку входа и карту файлов.
- [x] Зафиксировать голос канала, правила достоверности и структуру поста.
- [x] Заполнить очередь доказуемыми темами.
- [x] Добавить шаблоны пакета и черновика.
- [x] Описать работу с изображениями и публикационным реестром.

### Task 3: Зафиксировать последний успешный прогон

**Files:**
- Create: `docs/telegram/release-packets/2026-07-25-flowwow-verified-generation.md`
- Create: `docs/telegram/drafts/2026-07-26-flowwow-first-verified-draft.md`

- [x] Записать проверенные параметры запуска Flowwow и честные ограничения.
- [x] Подготовить стартовый черновик для Telegram-редактора.

### Task 4: Закрепить обновление контура

**Files:**
- Create: `AGENTS.md`

- [x] Добавить правило обновления журнала после значимых результатов.
- [x] Запретить перенос секретов и автоматическую публикацию.

### Task 5: Проверить структуру

**Files:**
- Verify: `AGENTS.md`
- Verify: `docs/product-journal/`
- Verify: `docs/telegram/`

- [x] Выполнить `git diff --check` без ошибок.
- [x] Проверить все относительные Markdown-ссылки.
- [x] Проверить JSON Lines синтаксис реестра публикаций.
- [x] Убедиться, что поиск по типичным секретам не находит значений.
