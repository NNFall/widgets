# AI Routing Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to execute this plan task-by-task.

**Goal:** Устранить подтверждённый production-сбой GPT-5.5 direction board:
правильно классифицировать timeout, добавить независимый Gemini fallback и
сократить время до безопасной деградации без повторного полного live-прогона.

**Architecture:** Provider adapters нормализуют transport deadlines в
`ProviderTimeout`. Routed engine является единой границей между
`ModelProviderError` и builder-domain `BuilderEngineError`. Runtime policy
задаёт provider-diverse ordered targets; fallback выполняется внутри одного
logical model call. Direction board сохраняет provider taxonomy и использует
provider-neutral публичный текст. Один монотонный deadline охватывает primary,
fallback и bounded cleanup, а приватный attempt ledger сохраняет фактические
provider/model/usage даже при неуспехе.

**Tech Stack:** Python 3.12, asyncio, SQLAlchemy, pytest, existing ModelRouter,
AgentRouter Qwen CLI, Gemini provider.

---

## Task 1: AgentRouter timeout taxonomy

**Files:**

- Modify: `tests/saas_cases/test_agentrouter_provider.py`
- Modify: `app/models/providers/agentrouter_qwen.py`

**Step 1: RED**

Добавить тест `test_agentrouter_deadline_raises_provider_timeout`: зависший
`communicate()` при коротком timeout должен завершить process tree и поднять
`ProviderTimeout` с `error_code == "generation_timeout"`.

**Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/saas_cases/test_agentrouter_provider.py -k "deadline_raises_provider_timeout" -q
```

Expected: FAIL, потому что текущий adapter поднимает `ProviderUnavailable`.

**Step 3: GREEN**

В timeout branch заменить provider-unavailable taxonomy на
`ProviderTimeout`. Не менять provider-wide failures и non-zero exit handling.

**Step 4: Verify GREEN**

Run:

```powershell
python -m pytest tests/saas_cases/test_agentrouter_provider.py -q
```

Expected: PASS.

## Task 2: Routed provider boundary

**Files:**

- Modify: `tests/builder_lab_cases/test_gemini_direct.py`
- Modify: `builder_lab/engines/gemini_direct.py`

**Step 1: RED**

Добавить тесты:

- `test_routed_provider_timeout_becomes_builder_generation_timeout`;
- `test_routed_provider_unavailable_becomes_provider_neutral_builder_error`;
- `test_routed_result_diagnostic_uses_actual_provider_and_model`.

Проверить сохранение billed usage и отсутствие слов `Gemini`, `GPT`, `GLM` в
публичном тексте ошибки.

**Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/builder_lab_cases/test_gemini_direct.py -k "routed_provider or routed_result_diagnostic" -q
```

Expected: FAIL из-за raw `ModelProviderError` и stale `self.model`.

**Step 3: GREEN**

Добавить один helper:

- вызывает router;
- переводит `ModelProviderError` в `BuilderEngineError`, сохраняя error code,
  diagnostic и billed usage;
- формирует provider-neutral русское public message;
- берёт фактические provider/model из `ModelResponse.raw`.

Все routed structured calls должны проходить через эту границу.

**Step 4: Verify GREEN**

Run:

```powershell
python -m pytest tests/builder_lab_cases/test_gemini_direct.py -q
```

Expected: PASS.

## Task 3: Direction board preserves infrastructure errors

**Files:**

- Modify: `tests/builder_lab_cases/test_directions.py`
- Modify: `builder_lab/directions.py`

**Step 1: RED**

Добавить
`test_parallel_provider_failure_preserves_infrastructure_code_and_cancels_siblings`.
Ожидания:

- `generation_timeout`, а не `invalid_artifact`;
- provider-neutral public message;
- две незавершённые sibling tasks отменены;
- judge не вызван;
- completed/billed usage не потерян.

**Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/builder_lab_cases/test_directions.py -k "preserves_infrastructure" -q
```

Expected: FAIL со старым Gemini-branded `invalid_artifact`.

**Step 3: GREEN**

`_board_error` должен сохранять builder-domain taxonomy и не создавать
provider-specific public text.

**Step 4: Verify GREEN**

Run:

```powershell
python -m pytest tests/builder_lab_cases/test_directions.py -q
```

Expected: PASS.

## Task 4: Provider-diverse runtime fallback

**Files:**

- Modify: `tests/builder_lab_cases/test_worker.py`
- Modify: `scripts/run_builder_worker.py`
- Modify: `.env.example`
- Modify: `docker-compose.yml`

**Step 1: RED**

Расширить runtime policy test:

- GPT decision roles: AgentRouter GPT primary, Gemini direct-model fallback;
- GLM generation roles: AgentRouter GLM primary, Gemini direct-model fallback;
- image/reference roles сохраняют image-capable Gemini;
- fallback provider должен отличаться от primary provider.

Добавить assertion, что обычный AgentRouter production timeout меньше 900
секунд, а 900 доступен только явному benchmark config.

**Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/builder_lab_cases/test_worker.py -k "runtime_router_maps or provider_diverse" -q
```

Expected: FAIL из-за singleton targets.

**Step 3: GREEN**

Скомпилировать ordered targets:

- `agentrouter/gpt-5.5 → gemini/direct_model`;
- `agentrouter/glm-5.2 → gemini/direct_model`.

Начальный production timeout AgentRouter: 180 секунд. Для benchmark
используется отдельная явная переменная/CLI option, без изменения production
default.

**Step 4: Verify GREEN**

Run:

```powershell
python -m pytest tests/builder_lab_cases/test_worker.py tests/saas_cases/test_model_router.py tests/builder_lab_cases/test_config.py -q
```

Expected: PASS.

## Task 5: Coherent deadlines, retry budget and fallback matrix

**Files:**

- Modify: `app/models/router.py`
- Modify: `app/models/contracts.py`
- Modify: `app/models/providers/gemini.py`
- Modify: `builder_lab/visual_review.py`
- Modify: `builder_lab/worker.py`
- Modify: `builder_lab/config.py`
- Modify: `tests/saas_cases/test_model_router.py`
- Modify: `tests/saas_cases/test_gemini_provider.py`
- Modify: `tests/builder_lab_cases/test_visual_review.py`
- Modify: `tests/builder_lab_cases/test_worker.py`
- Modify: `tests/builder_lab_cases/test_config.py`

**Step 1: RED**

Добавить тесты:

- production границы `119/120/360/361/900`;
- primary timeout оставляет резерв и доходит до Gemini fallback;
- внешний visual-judge timeout не отменяет route раньше fallback;
- all-target exhaustion не умножает один дорогой stage на три полных запуска;
- Gemini `generation_timeout` становится `ProviderTimeout`;
- semantic invalid response получает один bounded correction/fallback, а
  deterministic safety/policy violation не запускает слепое переключение;
- cleanup зависшего provider process ограничен остатком общего deadline.

**Step 2: GREEN**

Ввести monotonic logical-invocation deadline 120–360 секунд, определить
per-target slices с резервом fallback и ограничить cleanup. `900` разрешить
только отдельному benchmark parser/config, который production worker не читает.
Terminal route exhaustion должен иметь отдельную retry policy и не попадать в
общий трёхкратный stage retry.

**Step 3: Offline integration**

Без внешних API воспроизвести полный путь:

`AgentRouter timeout → Gemini success → три direction candidates → judge`.

Отдельно проверить терминальный all-target exhaustion и сохранение уже
полученных usage/attempt данных.

## Task 6: Route-attempt provenance and neutral error surfaces

**Files:**

- Modify: `app/models/contracts.py`
- Modify: `app/models/router.py`
- Modify: `builder_lab/engines/gemini_direct.py`
- Modify: `builder_lab/directions.py`
- Modify: `builder_lab/worker.py`
- Modify: `frontend/src/studio/errors.ts`
- Modify: related backend and frontend tests

**Step 1: RED**

Проверить, что каждый logical invocation сохраняет ordered attempts:
provider, model, outcome, latency, usage и cost state
`reported|estimated|unknown|not_billed`. При success через fallback данные
первой платной/неоднозначной попытки не теряются.

Публичная ошибка не содержит названий провайдеров. Приватный diagnostic
содержит фактическую цепочку. `invalid_response` переводится в
`MODEL_INVALID_OUTPUT`.

**Step 2: GREEN**

Добавить structured terminal route error и attempt provenance. Если
authoritative persistence переносится в forensic migration, hybrid routing
остаётся feature-flags-off до её внедрения.

## Task 7: Fail-closed rollout

**Files:**

- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: production smoke/readiness tests

**Step 1: Safe defaults**

Hybrid routing по умолчанию выключен. Production container принимает только
timeout 120–360; benchmark настройка не попадает в rendered production env.

**Step 2: Gates**

Перед enable выполнить:

1. deterministic fault injection;
2. rendered-config и executable/readiness check;
3. deploy с flags-off и no-model smoke;
4. только явно разрешённый bounded paid canary;
5. проверку attempt/audit chain и provider-neutral public error;
6. постепенное включение с проверенным kill switch/rollback.

## Task 8: Focused regression and documentation

**Files:**

- Modify: `docs/product-journal/2026-07.md`
- Modify: `docs/telegram/release-packets/` only if the result is independently
  publishable

**Step 1: Run focused suite**

```powershell
python -m pytest tests/saas_cases/test_agentrouter_provider.py tests/saas_cases/test_model_router.py tests/builder_lab_cases/test_gemini_direct.py tests/builder_lab_cases/test_directions.py tests/builder_lab_cases/test_worker.py -q
```

Expected: PASS with only documented environment skips.

**Step 2: Run broader bounded suite**

```powershell
python -m pytest tests/saas_cases tests/builder_lab_cases -q
```

Expected: PASS with documented skips.

**Step 3: Review**

Run:

```powershell
git diff --check
git status --short
```

Обновить product journal только подтверждёнными фактами. Не включать unrelated
Telegram dirty files.

**Step 4: Commit**

Создать отдельный scoped commit только после spec review и code-quality review.
Не включать hybrid routing в production этим коммитом, пока не пройдены все
gates Task 7 и не доказана route-attempt lineage.
