# Model-call Lineage, Cost Accounting and Stage Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to execute this plan task-by-task.

**Goal:** Сделать каждый фактический вызов модели воспроизводимым и финансово
однозначным: связать его с попыткой этапа, логическим запросом, кандидатом/persona,
семантической попыткой и fallback-цепочкой; не терять failed/timeout/cancelled
попытки; показывать владельцу безопасные итоги, администратору — полный waterfall;
сравнивать GPT-5.5, GLM-5.2 и Gemini по конкретным этапам и publishability, а не по
предполагаемой цене модели.

**Architecture:** `generation_stage_attempts` становится границей одной фактической
попытки builder-этапа, а `model_calls` — CAS-ledger каждой provider-попытки:
отдельный insert `dispatched` и однонаправленная terminal finalization.
Один `logical_invocation_id` объединяет primary и fallback, но каждая отправка
провайдеру имеет отдельный `ModelCall.id` и `fallback_index`. Явный
`ModelInvocationContext` передаётся через router и builder-код; `ModelRequest.metadata`
не является источником истины для учёта. Стоимость хранится вместе с состоянием
`reported | estimated | unknown | not_billed`; `unknown` никогда не трактуется как
ноль. Owner projection агрегирует только разрешённые поля, admin waterfall читает
ledger без повторных вызовов моделей. Benchmark использует замороженные одинаковые
входы и выводит рекомендацию только после технических, browser и visual gates.

**Tech Stack:** Python 3.12, asyncio, dataclasses, aiohttp, SQLAlchemy async,
PostgreSQL 15, Alembic, pytest, существующие `ModelRouter`, builder worker,
AgentRouter Qwen CLI и Gemini adapter.

---

## Обязательные границы и порядок

- Сначала завершить и принять
  `docs/superpowers/plans/2026-07-30-ai-routing-reliability.md`. Этот план расширяет
  уже существующую ordered fallback-логику, а не создаёт второй router.
- Перед первым изменением выполнить `python -m alembic heads`. Ожидаемый текущий
  head до объединения соседних планов — `0013_pattern_registry`.
- Предпочтительный путь: включить `generation_stage_attempts` и новые поля
  `model_calls` в ещё не применённую и не опубликованную
  `0014_generation_forensics`. Это один согласованный additive schema cut.
- Единственный schema-owner `0014` — план generation forensics. Этот план
  поставляет contracts и тестовые ожидания, но не создаёт вторую версию файла,
  отдельный fingerprint или независимый commit миграции.
- Если `0014_generation_forensics` уже применена хотя бы в одной общей среде, её
  запрещено переписывать. Остановиться, проверить все heads и создать одну линейную
  миграцию после фактического canonical head. Если к тому моменту существуют
  зарезервированные `0015`–`0017`, следующая миграция должна продолжить эту цепочку,
  а не создавать второй head или merge revision.
- Реализацию вести в отдельной ветке/worktree после синхронизации с владельцами
  планов forensics, billing, projects и funnel. Не переносить несвязанные dirty
  изменения.
- Ни один тест не вызывает платный API. Первый live benchmark и canary — отдельный
  явно разрешённый acceptance-шаг после schema preflight.
- Не сохранять prompt, response body, изображения, cookies, OAuth/API secrets или
  исходный URL в `model_calls`. Эти данные относятся к отдельному redacted forensic
  контуру.
- Thinking tokens являются подмножеством output tokens и не складываются с output
  при расчёте цены. Cache read/write — отдельные диагностические подмножества input.
- `provider`/`model` остаются запрошенным target для совместимости.
  `actual_provider`/`actual_model` заполняются только из верифицированных данных
  adapter-а; на timeout их нельзя угадывать.

## Целевая схема данных

### `generation_stage_attempts`

| Поле | Контракт |
|---|---|
| `id UUID PK` | Равен `RunClaim.attempt_id` |
| `run_id UUID FK CASCADE` | Запуск, обязательный индекс |
| `stage VARCHAR(64)` | Значение builder `Stage` |
| `ordinal INTEGER` | 1-based номер попытки именно этого stage |
| `status VARCHAR(32)` | `running`, `result_staged`, `completed`, `failed`, `interrupted`, `cancelled`, `accounting_failed` |
| `started_at`, `finished_at` | UTC; `finished_at` null только для незавершённой попытки |
| `created_at` | Server timestamp |

Ограничения: `ordinal > 0`, уникальность `(run_id, stage, ordinal)` и
`UNIQUE(id, run_id)` для composite membership. Ordinal
выделяется под блокировкой строки `GenerationRun`; `stage_retry_count` нельзя
использовать как источник ordinal, потому что lease recovery может создать новую
попытку без обычного retry transition.

### Новые поля `model_calls`

| Поле | Контракт |
|---|---|
| `stage_attempt_id UUID` | Null только для chat/legacy/non-run calls; вместе с `run_id` образует composite FK на `generation_stage_attempts(id, run_id)` |
| `logical_invocation_id UUID` | Один ID на всю target/fallback цепочку |
| `operation VARCHAR(64)` | Нормализованная операция этапа |
| `semantic_attempt INTEGER` | 1-based исправление/повтор смысла запроса |
| `candidate_id VARCHAR(64) NULL` | Например `candidate-1` |
| `persona VARCHAR(64) NULL` | Например роль direction/critic |
| `fallback_index INTEGER` | 1-based target внутри logical invocation |
| `actual_provider VARCHAR(64) NULL` | Фактически подтверждённый provider |
| `actual_model VARCHAR(128) NULL` | Фактически подтверждённая model |
| `cache_read_tokens BIGINT` | Неотрицательное подмножество input |
| `cache_write_tokens BIGINT` | Неотрицательное подмножество input |
| `cost_state VARCHAR(16)` | `reported`, `estimated`, `unknown`, `not_billed` |

Существующий `attempt` временно сохраняется и записывается равным
`fallback_index`. Уникальность `(logical_invocation_id, fallback_index)` запрещает
двойную запись одной target-попытки. `cost_microusd` становится nullable:
`unknown => NULL`, `not_billed => 0`, а `reported/estimated => non-null`.

Backfill старых строк:

- `logical_invocation_id = id`, `fallback_index = max(attempt, 1)`,
  `semantic_attempt = 1`, `operation = 'legacy_unclassified'`;
- `provider_dispatched = false` → `not_billed`, cost `0`;
- только доказанно ненулевые usage и полный rate-card snapshot → `estimated`;
- dispatch был, но данных для расчёта нет → `unknown`, cost `NULL`;
- `reported` старым строкам не присваивать;
- `actual_provider`, `actual_model`, `stage_attempt_id` не угадывать.
- legacy `failed + generation_timeout` нормализуется в `timed_out` либо
  агрегируется как timeout; нулевые defaults не считаются доказательством usage.

Дополнительные DB invariants:

- `stage_attempt_id IS NULL OR run_id IS NOT NULL`;
- composite FK запрещает связать model call запуска A с attempt запуска B;
- `cost_microusd IS NULL OR cost_microusd >= 0`;
- global unique `(logical_invocation_id, fallback_index)`.

## Неподвижная CAS-семантика ledger

- `insert_dispatched()` создаёт только новую row и immutable lineage.
- `finalize()` выполняет
  `UPDATE ... WHERE id=:id AND status='dispatched'`.
- Terminal row никогда не возвращается в `dispatched`.
- Повтор идентичной terminal finalization идемпотентен.
- Конфликтующая повторная finalization поднимает `ModelAccountingError`.
- Terminal update не меняет `run_id`, `stage_attempt_id`,
  `logical_invocation_id`, `operation`, semantic/candidate/persona и
  `fallback_index`.

---

## Task 0: Зафиксировать prerequisites и единственный migration path

**Files:**

- Read: `AGENTS.md`
- Read: `docs/superpowers/specs/2026-07-30-kaigo-verifiable-mvp-design.md`
- Read: `docs/superpowers/plans/2026-07-30-ai-routing-reliability.md`
- Read: `docs/superpowers/plans/2026-07-30-generation-events-forensics.md`
- Inspect: `migrations/versions/`
- Inspect: `app/models/router.py`
- Inspect: `app/saas/models.py`

- [ ] **Step 1: Проверить базу**

Run:

```powershell
git status --short
python -m alembic heads
rg -n "0014_generation_forensics|generation_stage_attempts|logical_invocation_id" migrations app tests
```

Expected: один head; до начала соседней реализации — `0013_pattern_registry`.

- [ ] **Step 2: Выбрать migration path письменно**

В implementation journal записать один из двух фактов:

1. `0014` ещё не применена — schema этого плана входит в тот же
   `0014_generation_forensics.py`; или
2. `0014` уже immutable — зафиксирован единственный фактический head и согласован
   следующий линейный revision.

При нескольких heads или неизвестном deployment status остановить реализацию.

- [ ] **Step 3: Зафиксировать baseline**

Run:

```powershell
python -m pytest tests/saas_cases/test_model_router.py tests/saas_cases/test_schema.py -q
python -m pytest tests/builder_lab_cases/test_worker.py -q
```

Expected: PASS до новых тестов.

---

## Task 1: Добавить stage-attempt и model-call lineage в schema

**Files:**

- Modify: `app/saas/models.py`
- Modify preferred: `migrations/versions/0014_generation_forensics.py`
- Modify: `tests/saas_cases/test_generation_forensics_migration.py`
- Modify: `tests/saas_cases/test_schema.py`
- Modify: `scripts/preflight_saas_schema.py`

- [ ] **Step 1: RED — описать schema contract**

Добавить тесты:

```python
def test_model_call_lineage_schema_is_present(inspector):
    columns = {row["name"]: row for row in inspector.get_columns("model_calls")}
    assert {
        "stage_attempt_id",
        "logical_invocation_id",
        "operation",
        "semantic_attempt",
        "candidate_id",
        "persona",
        "fallback_index",
        "actual_provider",
        "actual_model",
        "cache_read_tokens",
        "cache_write_tokens",
        "cost_state",
    } <= columns.keys()


def test_stage_attempt_has_unique_run_stage_ordinal(inspector):
    assert "generation_stage_attempts" in inspector.get_table_names()
    uniques = inspector.get_unique_constraints("generation_stage_attempts")
    assert any(
        set(item["column_names"]) == {"run_id", "stage", "ordinal"}
        for item in uniques
    )
```

Добавить migration test для upgrade с реальными legacy `model_calls` всех четырёх
backfill-категорий и downgrade без потери старых колонок.

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/saas_cases/test_generation_forensics_migration.py tests/saas_cases/test_schema.py -q
```

Expected: FAIL — таблицы и колонок ещё нет.

- [ ] **Step 3: GREEN — реализовать одну линейную миграцию**

Создать ORM `GenerationStageAttempt`, связи и индексы. В миграции:

1. создать stage-attempt table;
2. добавить nullable lineage/cost columns;
3. выполнить детерминированный backfill;
4. добавить defaults/not-null там, где backfill завершён;
5. сделать `cost_microusd` nullable;
6. добавить check/unique constraints и индексы;
7. не менять forensic retention tables.

Cross-field checks:

```sql
semantic_attempt > 0
fallback_index > 0
cache_read_tokens >= 0
cache_write_tokens >= 0
thinking_tokens <= output_tokens
cache_read_tokens + cache_write_tokens <= input_tokens
(actual_provider IS NULL) = (actual_model IS NULL)
(cost_state = 'unknown' AND cost_microusd IS NULL)
OR
(cost_state IN ('reported', 'estimated', 'not_billed') AND cost_microusd IS NOT NULL)
```

- [ ] **Step 4: Проверить migration round-trip и head**

Run:

```powershell
python -m pytest tests/saas_cases/test_generation_forensics_migration.py tests/saas_cases/test_schema.py -q
python -m alembic heads
python scripts/preflight_saas_schema.py
```

Expected: PASS и ровно один head.

- [ ] **Step 5: Commit schema cut**

```powershell
git add app/saas/models.py migrations/versions/0014_generation_forensics.py tests/saas_cases/test_generation_forensics_migration.py tests/saas_cases/test_schema.py scripts/preflight_saas_schema.py
git commit -m "feat: add model call lineage schema"
```

---

## Task 2: Типизировать invocation, usage и cost-state

**Files:**

- Create: `app/models/lineage.py`
- Create: `app/models/costs.py`
- Modify: `app/models/contracts.py`
- Create: `tests/model_cases/test_model_costs.py`
- Modify: `tests/saas_cases/test_model_router.py`

- [ ] **Step 1: RED — зафиксировать value-object контракты**

Покрыть тестами:

- `ModelInvocationContext` требует непустой `operation` и
  `semantic_attempt >= 1`;
- candidate/persona остаются nullable и не извлекаются из metadata;
- один context может породить несколько fallback attempts;
- negative usage и `thinking > output` запрещены;
- `cache_read + cache_write > input` запрещено;
- status различает `completed`, `failed`, `timed_out`, `cancelled`;
- рассчитанная по rate-card цена имеет `estimated`, а не `reported`;
- dispatched timeout без billing usage имеет `unknown` и `None` cost;
- pre-dispatch capability rejection имеет `not_billed` и ноль;
- provider monetary charge имеет `reported`;
- cache rates участвуют без двойного учёта input.

Целевые value objects:

```python
@dataclass(frozen=True, slots=True)
class ModelInvocationContext:
    stage_attempt_id: UUID | None
    stage: str | None
    operation: str
    semantic_attempt: int = 1
    candidate_id: str | None = None
    persona: str | None = None


class CostState(StrEnum):
    REPORTED = "reported"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"
    NOT_BILLED = "not_billed"
```

`ModelUsage` расширить `cache_read_tokens` и `cache_write_tokens`.
`ModelResponse` и `BilledModelProviderError` получают типизированный billing signal:
`reported_cost_microusd | no_charge_confirmed`; два сигнала одновременно запрещены.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/model_cases/test_model_costs.py tests/saas_cases/test_model_router.py -q
```

Expected: FAIL на отсутствующих типах/полях.

- [ ] **Step 3: GREEN — реализовать единый cost resolver**

Порядок определения:

1. provider передал monetary charge → `reported`;
2. dispatch не состоялся или provider явно подтвердил no-charge → `not_billed`;
3. все usage buckets и замороженные rates известны → `estimated`;
4. иначе → `unknown`.

Pricing snapshot хранит валюту, источник, effective version и rates для uncached
input, cache read, cache write, output. Формула использует:

```text
uncached_input = input - cache_read - cache_write
estimated = uncached_input * input_rate
          + cache_read * cache_read_rate
          + cache_write * cache_write_rate
          + output * output_rate
```

Thinking отдельно не прибавляется.

- [ ] **Step 4: Verify GREEN**

```powershell
python -m pytest tests/model_cases/test_model_costs.py tests/saas_cases/test_model_router.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit typed contracts**

```powershell
git add app/models/lineage.py app/models/costs.py app/models/contracts.py tests/model_cases/test_model_costs.py tests/saas_cases/test_model_router.py
git commit -m "feat: define invocation and cost state contracts"
```

---

## Task 3: Сделать router ledger полным и fail-closed

**Files:**

- Modify: `app/models/router.py`
- Modify: `tests/saas_cases/test_model_router.py`
- Modify: `tests/saas_cases/test_trial_service.py`
- Modify: `tests/saas_cases/test_trial_settlement_recovery.py`

- [ ] **Step 1: RED — проверить одну logical invocation и все outcomes**

Добавить тесты, которые доказывают:

- router создаёт `logical_invocation_id` один раз до target loop;
- primary и fallback имеют разные `call_id`, общий logical ID и индексы `1, 2`;
- context/stage attempt копируются в каждую строку;
- timeout записан как `timed_out`, cancellation как `cancelled`;
- billed failure сохраняет usage/cache/request id;
- pre-dispatch row создаётся до `provider.generate`;
- terminal audit failure после dispatch прекращает run как accounting failure и не
  вызывает fallback, чтобы не создать неучтённый повторный расход;
- `response.raw.route_attempts` — только диагностическая копия, SQL ledger остаётся
  источником истины;
- route-calculated cost имеет `estimated`.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/saas_cases/test_model_router.py tests/saas_cases/test_trial_service.py tests/saas_cases/test_trial_settlement_recovery.py -q
```

Expected: FAIL.

- [ ] **Step 3: GREEN — расширить audit record и router**

Изменить сигнатуру:

```python
async def generate(
    *,
    role: str,
    mode: str,
    request: ModelRequest,
    context: ModelInvocationContext,
    run_id: UUID | None = None,
    timeout_seconds: float | None = None,
) -> ModelResponse:
```

`SqlModelCallAudit` обязан:

- insert `dispatched` только для новой row; terminal state меняется CAS
  `dispatched -> terminal`, без blind upsert;
- никогда не перезаписывать immutable lineage полями другого значения;
- фиксировать dispatch до сетевого вызова;
- сохранять terminal outcome shielded от caller cancellation;
- поднимать отдельный `ModelAccountingError`, если обязательная запись не
  сохранилась.

Старое поле `attempt` временно писать равным `fallback_index`.

- [ ] **Step 4: Исправить trial settlement**

Token ledger использует известные input/output buckets. Cost ledger создаётся
только для `reported`/`estimated`. `unknown` не превращается в нулевой расход:
settlement получает `quarantined`/`cost_pending`, а reconciliation видит конкретный
`ModelCall.id`. Автоматический повтор provider call из-за accounting uncertainty
запрещён.

- [ ] **Step 5: Verify GREEN**

```powershell
python -m pytest tests/saas_cases/test_model_router.py tests/saas_cases/test_trial_service.py tests/saas_cases/test_trial_settlement_recovery.py -q
```

Expected: PASS, включая cancellation/fault-injection.

- [ ] **Step 6: Commit router ledger**

```powershell
git add app/models/router.py tests/saas_cases/test_model_router.py tests/saas_cases/test_trial_service.py tests/saas_cases/test_trial_settlement_recovery.py
git commit -m "feat: persist complete routed model attempts"
```

---

## Task 4: Нормализовать actual identity и cache usage в adapters

**Files:**

- Modify: `app/models/providers/agentrouter_qwen.py`
- Modify: `app/models/providers/gemini.py`
- Modify: `tests/saas_cases/test_agentrouter_provider.py`
- Modify: `tests/saas_cases/test_gemini_model_provider.py`

- [ ] **Step 1: RED — fixtures всех provider ответов**

Добавить offline fixtures/tests:

- AgentRouter `cache_read_input_tokens` попадает в `cache_read_tokens`;
- Gemini `cached_content_token_count` попадает в `cache_read_tokens`;
- thinking остаётся подмножеством output;
- подтверждённый response model version заполняет `actual_model`;
- target model не копируется в actual identity, если upstream этого не подтвердил;
- malformed/negative/cross-bucket usage отклоняется как `InvalidModelResponse`;
- billed provider error не теряет частичный usage.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/saas_cases/test_agentrouter_provider.py tests/saas_cases/test_gemini_model_provider.py -q
```

Expected: FAIL.

- [ ] **Step 3: GREEN — реализовать adapters без эвристик**

Заполнять actual identity только из документированного event/response metadata.
Если provider известен transport-ом, но модель не подтверждена, обе actual-колонки
оставлять null для соблюдения pair constraint.

- [ ] **Step 4: Verify and commit**

```powershell
python -m pytest tests/saas_cases/test_agentrouter_provider.py tests/saas_cases/test_gemini_model_provider.py -q
git add app/models/providers/agentrouter_qwen.py app/models/providers/gemini.py tests/saas_cases/test_agentrouter_provider.py tests/saas_cases/test_gemini_model_provider.py
git commit -m "feat: normalize provider identity and cache usage"
```

---

## Task 5: Ввести lifecycle `GenerationStageAttempt`

**Files:**

- Modify: `builder_lab/worker.py`
- Modify: `tests/builder_lab_cases/test_worker.py`
- Modify: `tests/builder_lab_cases/test_worker_recovery.py`

- [ ] **Step 1: RED — state-machine tests**

Проверить:

- claim под lock создаёт `running` attempt с ordinal 1;
- обычный retry того же stage получает ordinal 2;
- staged result переводит attempt в `result_staged`;
- успешный finalize переводит именно тот attempt в `completed`;
- stage exception/cancel помечают `failed`/`cancelled`;
- expired lease помечает прежний attempt `interrupted` до нового claim;
- recovery существующего staged result продолжает исходный attempt ID;
- ambiguous dispatched claim завершается `accounting_failed`;
- stale worker не может завершить чужую/более новую попытку.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/builder_lab_cases/test_worker.py tests/builder_lab_cases/test_worker_recovery.py -q
```

Expected: FAIL.

- [ ] **Step 3: GREEN — реализовать lifecycle транзакционно**

Все смены stage-attempt status выполнять в той же DB transaction, что и
соответствующий run/event transition. `worker_id` и lease token остаются в private
forensics, а не в product accounting table.

- [ ] **Step 4: Verify and commit**

```powershell
python -m pytest tests/builder_lab_cases/test_worker.py tests/builder_lab_cases/test_worker_recovery.py -q
git add builder_lab/worker.py tests/builder_lab_cases/test_worker.py tests/builder_lab_cases/test_worker_recovery.py
git commit -m "feat: persist generation stage attempts"
```

---

## Task 6: Передать semantic lineage во все generation call sites

**Files:**

- Modify: `app/models/structured_generation.py`
- Modify: `scripts/run_builder_worker.py`
- Modify: `scripts/analyze_reference_site.py`
- Modify: `builder_lab/directions.py`
- Modify: `builder_lab/engines/gemini_direct.py`
- Modify: `builder_lab/patterns/planner.py`
- Modify: `builder_lab/visual_critic.py`
- Modify: `builder_lab/visual_review.py`
- Modify: `builder_lab/visual_gate.py`
- Modify: `app/chat/service.py`
- Modify: `tests/builder_lab_cases/test_routed_reference_analysis.py`
- Modify: `tests/builder_lab_cases/test_directions.py`
- Modify: `tests/builder_lab_cases/test_gemini_direct.py`
- Modify: `tests/builder_lab_cases/test_visual_review.py`
- Modify: `tests/builder_lab_cases/test_visual_repair_gate.py`
- Modify: `tests/saas_cases/test_project_chat_lifecycle.py`

- [ ] **Step 1: RED — построить ожидаемую lineage matrix**

Тестами зафиксировать операции:

| Builder work | `operation` | candidate/persona |
|---|---|---|
| reference | `reference_analysis`, `schema_correction` | null |
| direction board | `direction_candidate`, `direction_judge` | `candidate-1..3`, DirectionRole |
| composition | `composition_plan` | selected candidate/persona |
| artifact | `artifact_generation`, `validation_repair` | selected context |
| visual | `visual_critic`, `visual_judge`, `visual_repair`, `repair_verification` | critic persona |
| independent code review | `code_review` | reviewer persona |
| visitor chat | `chat` | null; stage attempt null |

Provider transport retry/fallback не увеличивает `semantic_attempt`. Новый
исправленный prompt/schema correction/repair увеличивает его.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/builder_lab_cases/test_routed_reference_analysis.py tests/builder_lab_cases/test_directions.py tests/builder_lab_cases/test_gemini_direct.py tests/builder_lab_cases/test_visual_review.py tests/builder_lab_cases/test_visual_repair_gate.py tests/saas_cases/test_project_chat_lifecycle.py -q
```

Expected: FAIL.

- [ ] **Step 3: GREEN — передавать typed context**

Создавать базовый context из `RunClaim` в worker wiring и использовать
`dataclasses.replace` для operation/semantic/candidate/persona. Запретить
production generation с `run_id` без `stage_attempt_id`, когда feature flag
`KAIGO_MODEL_LINEAGE_REQUIRED=1`.

- [ ] **Step 4: Verify and commit**

```powershell
python -m pytest tests/builder_lab_cases tests/saas_cases/test_project_chat_lifecycle.py -q
git add app/models/structured_generation.py scripts/run_builder_worker.py scripts/analyze_reference_site.py builder_lab/directions.py builder_lab/engines/gemini_direct.py builder_lab/patterns/planner.py builder_lab/visual_critic.py builder_lab/visual_review.py builder_lab/visual_gate.py app/chat/service.py tests/builder_lab_cases tests/saas_cases/test_project_chat_lifecycle.py
git commit -m "feat: propagate semantic model call lineage"
```

---

## Task 7: Связать calls с artifact и построить безопасные aggregates

**Files:**

- Create: `app/models/accounting.py`
- Modify: `builder_lab/worker.py`
- Create: `tests/model_cases/test_model_accounting.py`
- Modify: `tests/builder_lab_cases/test_worker.py`

- [ ] **Step 1: RED — artifact and aggregate invariants**

Проверить:

- при materialize/finalize artifact все calls точного `stage_attempt_id` получают
  `artifact_id`, если он был null;
- stale stage attempt не привязывается к новой revision;
- failed calls сохраняются в totals;
- owner known cost разделён на reported/estimated;
- unknown attempts дают `cost_complete=false`, а не прибавляют ноль;
- logical invocation count и provider attempt count различаются;
- thinking не удваивает output;
- cache buckets отображаются отдельно.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/model_cases/test_model_accounting.py tests/builder_lab_cases/test_worker.py -q
```

Expected: FAIL.

- [ ] **Step 3: GREEN — один query layer**

`app/models/accounting.py` предоставляет:

- `aggregate_owner_run_usage(run_id)`;
- `load_admin_model_waterfall(run_id)`;
- `validate_run_lineage(run_id)`.

Worker привязывает artifact одним scoped update:

```python
update(ModelCall).where(
    ModelCall.stage_attempt_id == claim.attempt_id,
    ModelCall.artifact_id.is_(None),
).values(artifact_id=artifact.id)
```

- [ ] **Step 4: Verify and commit**

```powershell
python -m pytest tests/model_cases/test_model_accounting.py tests/builder_lab_cases/test_worker.py -q
git add app/models/accounting.py builder_lab/worker.py tests/model_cases/test_model_accounting.py tests/builder_lab_cases/test_worker.py
git commit -m "feat: aggregate model usage by stage and artifact"
```

---

## Task 8: Добавить privacy-safe owner totals

**Files:**

- Modify: `app/projects/routes.py`
- Modify: `app/projects/serializers.py`
- Modify: `tests/saas_cases/test_project_routes.py`

- [ ] **Step 1: RED — ownership и privacy projection**

Добавить `GET /api/projects/{project_id}/runs/{run_id}/model-usage`.
Тесты требуют ownership scope и следующий allowlist:

Route обязан проверять одновременно owner/tenant и
`GenerationRun.project_id == path project_id`; запуск другого проекта того же
пользователя возвращает 404.

```json
{
  "logical_invocations": 0,
  "provider_attempts": 0,
  "outcomes": {
    "completed": 0,
    "failed": 0,
    "timed_out": 0,
    "cancelled": 0
  },
  "tokens": {
    "input": 0,
    "output": 0,
    "thinking": 0,
    "cache_read": 0,
    "cache_write": 0
  },
  "known_cost_microusd": 0,
  "reported_cost_microusd": 0,
  "estimated_cost_microusd": 0,
  "unknown_cost_attempts": 0,
  "cost_complete": true
}
```

Ответ не содержит provider/model, request IDs, candidate/persona, errors, prompts,
source URL или forensic paths. Cross-user access возвращает 404.

- [ ] **Step 2: Verify RED, GREEN, verify**

```powershell
python -m pytest tests/saas_cases/test_project_routes.py -k "model_usage" -q
```

Expected before implementation: FAIL; after route/serializer: PASS.

- [ ] **Step 3: Commit owner projection**

```powershell
git add app/projects/routes.py app/projects/serializers.py tests/saas_cases/test_project_routes.py
git commit -m "feat: expose safe run model usage totals"
```

---

## Task 9: Добавить admin waterfall без повторного model call

**Files:**

- Modify/Create: `app/admin/generation_forensics.py`
- Modify: `app/admin/routes.py`
- Modify/Create: `tests/saas_cases/test_generation_forensics_admin.py`

- [ ] **Step 1: RED — nested waterfall**

Admin page группирует:

```text
stage attempt
  logical invocation: operation / semantic attempt / candidate / persona
    fallback 1: target -> actual / outcome / latency / tokens / cost state
    fallback 2: target -> actual / outcome / latency / tokens / cost state
```

Тесты проверяют ordered fallback, failed/timeout/cancel rows, unknown-cost marker,
accounting_failed marker, no model provider invocation и отсутствие prompt/raw
response/source URL.

- [ ] **Step 2: Verify RED, implement, verify**

```powershell
python -m pytest tests/saas_cases/test_generation_forensics_admin.py -q
```

Expected before implementation: FAIL; after implementation: PASS.

- [ ] **Step 3: Commit admin waterfall**

```powershell
git add app/admin/generation_forensics.py app/admin/routes.py tests/saas_cases/test_generation_forensics_admin.py
git commit -m "feat: add admin model call waterfall"
```

---

## Task 10: Создать provider-neutral stage benchmark contract

**Files:**

- Create: `app/models/benchmark.py`
- Create: `benchmarks/model-routing/stage-suite-v1/manifest.json`
- Create: `tests/model_cases/test_model_stage_benchmark.py`
- Modify: `docs/model-benchmarks/README.md`

- [ ] **Step 1: RED — benchmark semantics**

Замороженный suite содержит минимум три privacy-safe case:

1. information-heavy service;
2. brand-heavy commerce;
3. minimal landing.

Manifest фиксирует hashes входов, prompt versions, pattern-registry digest,
stage/role, repetitions и budget. Исходные URLs, пользовательские prompts и
скриншоты в public manifest не входят.

Тесты требуют:

- одинаковые fixtures для GPT-5.5, GLM-5.2 и Gemini;
- capability gate: image stages помечаются `unsupported`, а не failure;
- stage success, validator, browser, visual score и publishability отдельно;
- repair/fallback rates, median/p95 latency, token/cache buckets;
- reported/estimated/unknown/not-billed counts;
- recommendation может быть `insufficient_evidence`;
- GLM может оказаться дороже GPT даже при меньшей unit price;
- модель не повышается по одной цене без publishability;
- report builder не меняет runtime policy.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/model_cases/test_model_stage_benchmark.py -q
```

Expected: FAIL.

- [ ] **Step 3: GREEN — pure report/ranking module**

Publishability для финального artifact истинна только при одновременном прохождении
technical validator, browser gate, visual gate и отсутствии unresolved critical
finding. Promotion требует:

- минимального числа повторов каждого поддерживаемого этапа;
- нулевого unknown cost в первом rollout;
- заданного publishability threshold без quality regression;
- затем сравнения latency и known cost.

Модуль возвращает рекомендацию, но не записывает `ModelPolicy`.

- [ ] **Step 4: Verify and commit**

```powershell
python -m pytest tests/model_cases/test_model_stage_benchmark.py -q
git add app/models/benchmark.py benchmarks/model-routing/stage-suite-v1/manifest.json tests/model_cases/test_model_stage_benchmark.py docs/model-benchmarks/README.md
git commit -m "feat: define stage specific model benchmark"
```

---

## Task 11: Перевести comparison runner на authoritative ledger

**Files:**

- Create: `scripts/compare_model_routes.py`
- Modify: `scripts/compare_builder_models.py`
- Modify: `tests/model_cases/test_compare_builder_models.py`
- Modify: `tests/model_cases/test_build_agent_kernel_public_comparison.py`
- Modify: `docs/model-benchmarks/README.md`

- [ ] **Step 1: RED — CLI и public allowlist**

Тестировать offline:

- `--validate-fixtures` не вызывает provider;
- `--resume` не повторяет уже terminal invocation;
- hard spend cap останавливает новые dispatch;
- итог читается из `model_calls`, а не из ручного счётчика процесса;
- public JSON/HTML исключают request IDs, prompts, URLs, raw responses и private
  paths;
- private operator report сохраняет stage waterfall и cost-state mix;
- старый frozen `2026-07-29-agent-kernel-frozen-v1` остаётся историческим и не
  переписывается.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest tests/model_cases/test_compare_builder_models.py tests/model_cases/test_build_agent_kernel_public_comparison.py -q
```

Expected: FAIL на новом authoritative contract.

- [ ] **Step 3: GREEN — реализовать runner**

CLI сначала валидирует manifest/capabilities/budget, затем создаёт отдельный
benchmark run. Он не предполагает, что GLM дешевле, и допускает итог
`insufficient_evidence`. Live mode требует отдельного `--confirm-paid-run` и не
используется в CI.

- [ ] **Step 4: Verify and commit**

```powershell
python scripts/compare_model_routes.py --manifest benchmarks/model-routing/stage-suite-v1/manifest.json --validate-fixtures
python -m pytest tests/model_cases/test_compare_builder_models.py tests/model_cases/test_build_agent_kernel_public_comparison.py -q
git add scripts/compare_model_routes.py scripts/compare_builder_models.py tests/model_cases/test_compare_builder_models.py tests/model_cases/test_build_agent_kernel_public_comparison.py docs/model-benchmarks/README.md
git commit -m "feat: benchmark model routes from audited calls"
```

---

## Task 12: Fail-closed rollout и финальная проверка

**Files:**

- Modify: `.env.example`
- Modify: `builder_lab/config.py`
- Modify: `scripts/preflight_saas_schema.py`
- Create: `scripts/audit_model_call_lineage.py`
- Create: `tests/saas_cases/test_model_lineage_preflight.py`
- Modify: `docs/OPERATIONS.md`
- Modify: `docs/TECHNICAL_CHANGELOG.md`

- [ ] **Step 1: RED — preflight and fault injection**

Проверить:

- `KAIGO_MODEL_LINEAGE_REQUIRED=1` запрещает generation dispatch без schema;
- каждый non-chat call с run ID имеет stage attempt;
- каждый logical invocation имеет непрерывные fallback indexes `1..N`;
- каждый dispatched row terminal либо явно отмечен stale для reconciliation;
- unknown cost блокирует `cost_complete`;
- accounting insert/finalize failure не запускает следующий provider;
- flag off не включает hybrid production policy автоматически.

- [ ] **Step 2: Verify RED, implement, verify**

```powershell
python -m pytest tests/saas_cases/test_model_lineage_preflight.py -q
python scripts/audit_model_call_lineage.py --check-schema --fail-on-open-attempts
```

Expected after implementation: PASS / exit 0 на тестовой БД.

- [ ] **Step 3: Полный локальный regression**

```powershell
python -m pytest tests/saas_cases tests/model_cases tests/builder_lab_cases -q
python -m alembic heads
python scripts/preflight_saas_schema.py
python scripts/compare_model_routes.py --manifest benchmarks/model-routing/stage-suite-v1/manifest.json --validate-fixtures
```

Expected: PASS, один migration head, ни одного сетевого model call.

- [ ] **Step 4: Разворачивать по слоям**

1. schema;
2. код с `KAIGO_MODEL_LINEAGE_REQUIRED=0`;
3. preflight и audit существующих строк;
4. включить flag на одном canary worker;
5. выполнить один bounded paid canary только после явного разрешения;
6. проверить waterfall, owner totals, trial settlement и unknown cost;
7. только затем расширять rollout;
8. stage benchmark запускается отдельно и лишь предлагает policy.

Kill switch отключает новые hybrid routes, но не удаляет ledger и не маскирует
unknown costs. При нарушении lineage новые платные dispatch fail closed.

- [ ] **Step 5: Документация и commit**

В `docs/OPERATIONS.md` описать cost states, reconciliation и команды audit.
В changelog отделить проверенные факты от планируемого live benchmark.

```powershell
git add .env.example builder_lab/config.py scripts/preflight_saas_schema.py scripts/audit_model_call_lineage.py tests/saas_cases/test_model_lineage_preflight.py docs/OPERATIONS.md docs/TECHNICAL_CHANGELOG.md
git commit -m "docs: add model lineage rollout controls"
```

---

## Definition of Done

- У каждого нового generation model call есть stage attempt, operation,
  semantic attempt и logical invocation.
- Primary/fallback attempts сохранены раздельно и упорядочены; failed, timeout и
  cancelled не теряются.
- Actual provider/model не выводятся эвристически.
- Usage включает input/output/thinking/cache buckets без двойного счёта.
- Стоимость каждой попытки имеет явное состояние; unknown не считается нулём.
- Trial settlement не списывает неизвестную стоимость и переводит её в
  reconciliation.
- Owner видит только privacy-safe totals; admin видит полный waterfall без prompt
  и raw provider body.
- Artifact связан только с calls своей stage attempt.
- PostgreSQL имеет один линейный Alembic head.
- Offline regression, migration round-trip, schema preflight и benchmark fixture
  validation проходят.
- GPT-5.5, GLM-5.2 и Gemini сравниваются по этапам и publishability; отчёт вправе
  не выбрать победителя и никогда сам не меняет production routing.
- Live API benchmark и production enablement остаются отдельными, явно
  подтверждаемыми операциями.
