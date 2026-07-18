# RAW BUREAU Verified Widget — план реализации

> Выполнять по задачам через TDD. После каждой задачи: spec review, затем code
> quality review; Critical/Important исправлять до перехода дальше.

**Цель:** заменить ложный статический public demo на компактный RAW BUREAU
AI-виджет с реальным двухходовым Gemini-чатом и добавить доказательный контур
`browser → screenshots/metrics → Gemini visual critic → bounded repair`.

**Архитектура:** сгенерированный HTML/CSS остаётся недоверенным и не получает
сеть. Версионированный trusted runtime внутри preview отвечает за UI-state и
общается с parent через nonce-bound `postMessage`; parent вызывает bounded
same-origin chat API. Финальная revision до commit проходит Playwright gate и
multimodal Gemini critic. Публичный RAW demo использует ту же границу доверия.

**Стек:** Python 3.11+, aiohttp, `google-genai`, Playwright Chromium, unittest,
Docker Compose, nginx, встроенный Browser skill Codex.

**Спецификация:**
`docs/superpowers/specs/2026-07-19-raw-bureau-verified-widget-design.md`

---

## Task 0. Visual Site Research Agent на готовой основе

**Файлы:**

- создать `builder_lab/reference_crawler.py`;
- создать `builder_lab/reference_models.py`;
- создать `scripts/capture_reference_site.py`;
- изменить `requirements.txt`;
- изменить `Dockerfile.builder-lab`;
- изменить `builder_lab/config.py`;
- тесты: `test_reference_models.py`, `test_reference_crawler.py`,
  `test_config.py`, `test_packaging.py`.

**Основа:** `crawlee[playwright]` + Chromium Playwright. Stagehand/Browser Use не
включаются в основной path; они остаются opt-in fallback после отдельной оценки.

### TDD

1. Failing tests на URL/redirect guard: только public HTTP(S), порты 80/443,
   блок private, loopback, link-local, metadata, credentials in URL, DNS rebinding.
2. Failing tests на bounded crawl: robots by default, max 5 pages, concurrency 1
   per host, byte/time/depth/retry caps, same-site URL deduplication.
3. Failing browser fixture с lazy image и scroll-reveal: initial screenshot пуст,
   после 5s warm-up + incremental scroll элементы видимы и входят в evidence.
4. Реализовать lifecycle: load/fonts/images → 5s configurable warm-up → scroll
   шагами 70% viewport/750ms (профиль 600–1200ms) → две stable-height итерации → return top →
   1.5s settle → top/middle/bottom tiles и bounded full-page evidence.
5. Собирать bounded computed-style sample и animation inventory, а не полный DOM:
   typography, colors, surfaces, radii, borders, shadows, spacing, controls,
   fixed/sticky collisions, image aspect ratios.
6. Desktop для max 5 key pages; mobile для homepage + одной content page;
   sitemap/nav priority: home, services/prices, portfolio/catalog, FAQ, contacts.
7. Сохранять только structured JSON и screenshot hashes в Git; raw screenshots
   живут во временном evidence storage и удаляются по TTL; Playwright trace
   сохраняется только для failed run.
8. Проверить focused/full tests и реальный capture RAW BUREAU во встроенном
   браузере как независимый контроль.
9. Однократно сравнить BrandProfile с Firecrawl Branding Format v2; использовать
   его как benchmark, а не обязательную cloud dependency.

**Commit:** `feat: add bounded visual reference crawler`

## Task 1. Контракты направления и визуального аудита

**Файлы:**

- создать `builder_lab/directions.py`;
- создать `builder_lab/visual_models.py`;
- изменить `builder_lab/models.py`;
- изменить `builder_lab/prompts.py`;
- изменить `builder_lab/engines/base.py`;
- изменить `builder_lab/engines/gemini_direct.py`;
- изменить `builder_lab/orchestrator.py`;
- тесты: `tests/builder_lab_cases/test_directions.py`,
  `test_visual_models.py`, `test_gemini_direct.py`, `test_orchestrator.py`.

### TDD

1. Написать failing tests на три независимых роли, blind judge, агрегирование
   usage и событие `direction.judged` до `art_direction`.
2. Написать failing tests на строгие `ScreenshotEvidence`, `LayoutEvidence`,
   `VisualFinding`, `VisualCritique`: enum, bounds, unique IDs, pass без
   blocker/major, stable fingerprint.
3. Расширить prompt обязательными правилами выбранного направления: `372px`,
   `68dvh`, mobile `70dvh`, no fullscreen, closed default, runtime message
   classes, не более двух first-open suggestions, отсутствие fake actions.
4. Реализовать Gemini direction board: три proposal calls параллельно и один
   judge call; structured output; bounded output; никакого agent recursion.
5. Передать выбранное решение всем пяти generation stages без изменения
   безопасного artifact schema.
6. Проверить focused tests и полный `pytest`.

**Commit:** `feat: add bounded direction board and visual contracts`

## Task 2. Реальный trusted chat runtime для preview/demo

**Файлы:**

- создать `builder_lab/chat.py`;
- изменить `builder_lab/preview.py`;
- изменить `builder_lab/demo.py`;
- изменить `builder_lab/web.py`;
- изменить `builder_lab/config.py`;
- изменить `scripts/run_builder_lab.py`;
- изменить `scripts/smoke_builder_lab.py`;
- тесты: `test_chat.py`, `test_preview.py`, `test_demo.py`, `test_web.py`,
  `test_runner.py`, `test_smoke_script.py`.

### TDD

1. Написать failing tests, что runtime больше не очищает input без результата,
   стартует закрытым, формирует protocol v2 `chat.request`, безопасно добавляет
   user/assistant через `textContent`, поддерживает pending/error/retry,
   Enter/Shift+Enter/IME, Escape/focus и session-preserving close/open.
2. Написать failing tests на `GeminiDemoChatService`: одна history на session,
   максимум 8 turns, 1000 символов, one in-flight, TTL/capacity, low thinking,
   max 384 output tokens, no tools, provider errors без потери retry text.
3. Добавить `/demo/chat` и `/api/runs/{run_id}/chat` с HttpOnly SameSite cookie,
   request IDs, JSON errors и server-side ключом.
4. Parent bridge валидирует `event.source`, protocol, channel, request/revision,
   payload length; iframe сохраняет `connect-src 'none'`.
5. Переделать public demo shell: widget — основной интерактивный объект, evidence
   metadata свёрнута; убрать 760px/680px hard-coding и ложные формулировки.
6. `BuilderDemo` хранит проверенный `source_url` и chat system prompt; старый v1
   читается безопасно, но маркируется visual-only до повторной генерации.
7. Добавить real-chat флаги в smoke script; позитивный evidence требует два
   assistant replies одной session.
8. Прогнать focused/full tests.

**Commit:** `feat: add real trusted chat bridge to builder preview`

## Task 3. Browser verifier и Gemini image critic

**Файлы:**

- создать `builder_lab/browser_audit.py`;
- создать `builder_lab/visual_critic.py`;
- создать `Dockerfile.builder-lab`;
- изменить `requirements.txt`;
- изменить `docker-compose.yml`;
- изменить `builder_lab/config.py`;
- изменить `scripts/run_builder_lab.py`;
- тесты: `test_browser_audit.py`, `test_visual_critic.py`, `test_config.py`,
  `test_packaging.py`.

### TDD

1. Добавить failing tests на два viewports и шесть screenshot IDs, DPR 1,
   deterministic two-turn fixture, metrics после turn 1, blocked network,
   console/page failures и SHA-256.
2. Реализовать Playwright harness над `build_preview_document()`; generated
   iframe не получает ключ/DB/cookies и не выполняет внешний network.
3. Детерминированно проверять bounds, 44px targets, first-open overflow,
   horizontal overflow, composer overlap, message order, close/reopen history и
   desktop/mobile height caps.
4. Добавить `GeminiVisualCritic` на `gemini-3.5-flash`: brief + compact metrics +
   шесть подписанных `types.Part.from_bytes(..., image/jpeg)`; strict JSON schema,
   semantic validation, 8 MB aggregate/1.5 MB per image.
5. Добавить unit probe с уникальным seed marker, чтобы fake client подтвердил
   порядок image parts; live probe позже должен назвать image-specific marker.
6. Создать отдельный builder image с Chromium; production app Dockerfile не
   утяжелять. Compose builder-lab использует новый Dockerfile.
7. Прогнать focused/full tests и один локальный real Chromium harness.

**Commit:** `feat: add browser evidence and Gemini visual critic`

## Task 4. Bounded visual repair gate в orchestrator

**Файлы:**

- изменить `builder_lab/orchestrator.py`;
- изменить `builder_lab/engines/base.py`;
- изменить `builder_lab/engines/gemini_direct.py`;
- изменить `builder_lab/store.py` при необходимости для отчёта;
- изменить `builder_lab/models.py` для public error/event;
- тесты: `test_orchestrator.py`, `test_store.py`, `test_gemini_direct.py`.

### TDD

1. Failing tests: visual gate вызывается только для `motion_polish`, после
   deterministic pass и до final commit.
2. Failing tests: `pass` commit-ит revision; blocker/major вызывает один полный
   artifact repair; minor не вызывает repair.
3. Failing tests: максимум 2 repairs/3 critic calls, repeated fingerprint,
   deterministic regression и exhaustion дают `visual_quality_failed`; последняя
   ранее committed revision сохраняется.
4. События: `visual_audit.started/completed`, `screenshot.captured`,
   `visual_repair.started/completed`, `visual_audit.passed/blocked`.
5. Synthetic transcript создаётся один раз и повторно используется после repair.
6. Прогнать focused/full tests.

**Commit:** `feat: gate final widget on visual browser audit`

## Task 5. RAW BUREAU reference packet и реальная генерация

**Файлы:**

- создать `scripts/analyze_reference_site.py`;
- создать `examples/raw-bureau/reference.json`;
- создать `examples/raw-bureau/chat-system-prompt.txt`;
- создать `examples/raw-bureau/generation-brief.txt`;
- изменить `scripts/smoke_builder_lab.py` для prompt/source args;
- изменить `docs/KAIGO_BUILDER_LAB_OPERATIONS.md`.

### Выполнение

1. Во встроенном Browser skill открыть RAW BUREAU и независимо сверить результат
   Task 0: post-load, scroll reveal, top/middle/bottom tiles, typography/colors.
2. `analyze_reference_site.py` принимает только явно разрешённый HTTPS URL и
   локальные bytes screenshot, проверяет MIME/size, отправляет image part Gemini,
   возвращает structured brand/content facts. Произвольный URL не передаётся
   Gemini как `file_uri`.
3. Reference JSON хранит только публичные факты, visual tokens, timestamp, source
   URL и screenshot hash; само чужое изображение в Git не добавляется.
4. Сформировать grounded chat prompt: не выдумывать цены/функции; не называть AI
   человеком; отвечать кратко; при неизвестном факте честно предлагать уточнение.
5. Запустить реальный direct generation через американский Gemini proxy path,
   сохранить все события/usage и финальный demo artifact.
6. Проверить, что direction board сделал 3 proposals + judge и что visual gate
   получил реальные image parts.

**Commit:** `docs: add RAW BUREAU generation evidence`

## Task 6. Deploy, GitHub и внешняя проверка

**Файлы/системы:**

- GitHub branch `codex/gemini-technical-foundation`;
- сервер `/root/ai_project`;
- `/etc/nginx/sites-available/kaigo.space` только если нужен новый exact route;
- `data/builder-demo/latest.json`;
- operation docs.

### Выполнение

1. До deploy: `git status`, `git diff --check`, `python -m compileall`, полный
   `pytest`, Compose config, builder image build.
2. Commit/push только scoped changes; сохранить unrelated server files.
3. На сервере: fetch/fast-forward, build builder-lab image, запустить profile,
   выполнить real generation и сохранить demo.
4. При изменении nginx: timestamped backup, `nginx -t`, reload; не трогать
   статический сайт и соседний realtime service.
5. HTTP smoke: `/`, `/builder-demo/`, `/builder-demo/preview`, `/builder/`,
   `/w/demka`, `/api/health`, соседние домены.
6. Через встроенный Browser skill, а не внешний Chrome:
   - desktop `1440×900`: closed, open, turn 1, turn 2, close/reopen;
   - mobile `390×844`: те же состояния, bounds/keyboard/overflow;
   - timeout/offline/retry;
   - visible screenshots полного viewport.
7. В отдельной временной browser-сессии наложить live widget iframe поверх
   открытого `rawbureau.ru` и проверить collision/site fit; ничего на чужом сайте
   не сохранять.
8. Отправить screenshot bytes Gemini critic и сохранить structured report с
   model/request ID, usage и итоговым verdict.
9. Проверить server logs: две реальные chat requests, одна session, history 4
   messages, provider model и отсутствие 5xx.
10. Если любой P0 gate не проходит, не называть результат готовым и продолжить
    repair в пределах утверждённых лимитов.

**Commit:** `docs: record RAW BUREAU live verification`

## Итоговые команды проверки

```powershell
python -m pytest -q
python -m compileall builder_lab app scripts tests
git diff --check
docker compose --profile builder-lab config --quiet
docker compose --profile builder-lab build builder-lab
python scripts/smoke_builder_lab.py --base-url http://127.0.0.1:8091 `
  --brief-file examples/raw-bureau/generation-brief.txt `
  --source-url https://rawbureau.ru/ `
  --chat-prompt-file examples/raw-bureau/chat-system-prompt.txt `
  --demo-output data/builder-demo/latest.json `
  --require-real-chat --require-visual-audit
```

План завершён только после live URL, двух реальных ходов, desktop/mobile evidence,
Gemini image-specific critique, server log correlation и push в GitHub.
