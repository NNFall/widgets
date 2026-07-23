# Direct Chat-First Widget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Сделать Direct-генерацию индивидуальных виджетов, которые всегда читаются
как реальный чат и проходят проверку динамических сообщений.

**Architecture:** Fixed preview runtime владеет сообщениями и состоянием разговора,
Gemini 3.6 Flash владеет визуальным оформлением. Промпт и Chromium gate разделяют
один chat-first DOM-контракт.

**Tech Stack:** Python 3.11, unittest, aiohttp, Playwright Chromium, Gemini 3.6 Flash,
HTML/CSS/JavaScript.

---

### Task 1: Зафиксировать benchmark

**Files:**
- Create: `PRODUCT.md`
- Create: `docs/RAW_BUREAU_COMPARISON_V1.md`
- Create: `docs/evidence/raw-bureau-comparison-v1/*`

- [ ] Сохранить usage, время, стоимость, публичные URL и digest входных данных.
- [ ] Сохранить desktop/mobile изображения Direct и Antigravity.
- [ ] Проверить, что v1 не перезаписывается новой генерацией.
- [ ] Закоммитить документацию benchmark.

### Task 2: Описать chat-first контракт тестами

**Files:**
- Modify: `tests/builder_lab_cases/test_gemini_direct.py`
- Modify: `tests/builder_lab_cases/test_preview.py`
- Modify: `tests/builder_lab_cases/test_browser_audit.py`

- [ ] Добавить тест, требующий в stage prompt runtime selectors, bubbles, стороны
  разговора, короткое приветствие и максимум две начальные подсказки.
- [ ] Добавить тест stable runtime classes для user, assistant, status и content.
- [ ] Добавить Chromium assertion, что suggestions скрыты после первого user turn.
- [ ] Добавить Chromium test, который отклоняет одинаковый полноширинный transcript.
- [ ] Запустить точечные тесты и убедиться, что они падают по отсутствующему контракту.

### Task 3: Исправить runtime

**Files:**
- Modify: `builder_lab/preview.py`

- [ ] Добавить класс `.kaigo-widget__message` и role modifier каждому динамическому
  сообщению.
- [ ] Добавить стабильные классы label/content/status.
- [ ] После первого сообщения выставить `data-chat-started="true"` и скрыть весь
  suggestions region.
- [ ] Запустить runtime-тесты до зелёного состояния.

### Task 4: Исправить Direct prompts

**Files:**
- Modify: `builder_lab/prompts.py`

- [ ] Убрать запрет на bubbles и avatars.
- [ ] Потребовать единый стиль статического welcome и runtime messages.
- [ ] Потребовать AI слева, пользователя справа и видимые подписи.
- [ ] Ограничить первый экран одним коротким welcome и двумя quick replies.
- [ ] Запретить постоянные facts/pricing/menu blocks вне разговора.
- [ ] Сохранить свободу размеров, анимаций, JavaScript и арт-дирекции.
- [ ] Запустить prompt-тесты до зелёного состояния.

### Task 5: Усилить Chromium gate

**Files:**
- Modify: `builder_lab/visual_models.py`
- Modify: `builder_lab/browser_audit.py`
- Modify: `tests/builder_lab_cases/test_browser_audit.py`

- [ ] Добавить bounded `chat_visual_states` в `LayoutEvidence`.
- [ ] Измерить left/right gaps, width ratio, label visibility и style signatures.
- [ ] На after-turn состояниях требовать противоположные стороны, ограниченную ширину,
  различимые роли и подписи.
- [ ] На after-turn состояниях отклонять видимые suggestions.
- [ ] Прогнать все browser audit tests.

### Task 6: Сгенерировать Direct v2

**Files:**
- Create: отдельный output directory `direct-chat-v2`
- Modify: comparison publishing artifacts only after successful audit

- [ ] Развернуть обновлённый Direct builder.
- [ ] Запустить тот же RAW BUREAU input bundle с Gemini 3.6 Flash и high thinking.
- [ ] Дождаться пяти этапов, repairs, Chromium audit и visual critic.
- [ ] Не заменять Direct v1.
- [ ] Опубликовать v2 на отдельном URL.

### Task 7: Проверить и выпустить

**Files:**
- Modify: `docs/RAW_BUREAU_COMPARISON_V1.md` или создать v2 report.

- [ ] Во встроенном браузере проверить desktop open, два вопроса, close/reopen.
- [ ] Проверить mobile 390×844 и отсутствие почти полноэкранной панели.
- [ ] Сохранить новые screenshots и usage.
- [ ] Запустить полный релевантный test suite.
- [ ] Закоммитить, push в GitHub, развернуть на сервере и проверить публичный URL.
