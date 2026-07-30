# Generation Events and Five-Day Forensics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить единый типизированный реестр generation events и приватный
forensic-контур на пять суток, чтобы оператор мог безопасно восстановить ход
запуска, а owner API продолжал отдавать только разрешённую проекцию событий.

**Architecture:** Существующий `generation_events` остаётся единственным
источником порядка: его `sequence` не дублируется файловым журналом. Новый
registry валидирует все новые типы, строит per-type public projection и отделяет
операционный payload от приватных доказательств. Первый безопасный runtime
slice ограничен registry, recursive redaction, public projection и объединённой
schema `0014`; filesystem evidence, admin timeline и cleanup остаются
выключенными до отдельного security gate. После gate текстовые приватные данные
рекурсивно обезличиваются до записи и сохраняются вместе со снимками на
persistent volume; PostgreSQL хранит только метаданные manifest, относительные
ссылки и текущий статус. Core runs/events/artifacts/model-call accounting живут
по product retention. Admin timeline никогда не вызывает модель и доступен
только доказанному оператору, а не самозаявленному email.

**Tech Stack:** Python 3.11+, `StrEnum`, asyncio, aiohttp, SQLAlchemy async,
PostgreSQL 15, Alembic, pytest, Docker Compose, systemd.

---

## Предпосылки и границы

- Выполнять этот план только после завершения
  `docs/superpowers/plans/2026-07-30-ai-routing-reliability.md`: forensic hook в
  `ModelRouter` должен накладываться на уже исправленную timeout/fallback
  семантику, а не создавать параллельную ветку router logic.
- На старте `alembic heads` обязан возвращать ровно
  `0013_pattern_registry`. Следующая миграция намеренно и неизменно называется
  `0014_generation_forensics` с `down_revision = "0013_pattern_registry"`. Если
  к моменту исполнения появился другой head, остановиться и согласовать
  последовательность; не перенумеровывать миграцию молча.
- Этот план является единственным schema-owner файла
  `0014_generation_forensics.py`. Миграция одновременно включает forensics и
  model-call lineage/cost contracts из соседнего плана. До применения
  существует один combined migration test и один финальный schema fingerprint;
  второй план не редактирует уже проверенную `0014` независимо.
- Работать в отдельной ветке/worktree `codex/generation-forensics`. Не переносить
  туда несвязанные dirty-изменения текущего checkout и не менять их.
- Feature flag по умолчанию выключен. Создание schema и безопасная public
  projection не включают запись приватных данных автоматически.
- Не менять funnel events (`composer_submitted`, `run_queued`, `first_artifact`,
  `free_result`), их retention или `record_funnel_event`.
- Не заменять существующий одночасовой `reference-evidence` cleanup. Новый
  пятидневный storage, marker, timer и volume добавляются рядом с ним; текущий
  `builder_lab/reference_storage.py` используется только как проверенный
  источник атомарной приватной записи.
- Не переносить в этот срез billing, project-versioning, benchmark/routing
  policy или production rollout. Установка и включение timer на реальном host
  требуют отдельного операторского шага после локальной проверки.
- Не писать в forensic-слой API/OAuth tokens, cookies, passwords,
  authorization headers или исходные provider credentials. Model input images
  в первом срезе представлены только `sha256`, MIME/размером и количеством;
  байтами сохраняются только созданные `BrowserAudit` JPEG screenshots.
- Filesystem recorder/admin/cleanup нельзя включать, пока не доказаны:
  PostgreSQL advisory lock per run; global cleanup advisory lock; атомарный
  rename каталога в внутренний `.trash`; no-follow deletion; tombstone для
  отсутствующего/ранне удалённого evidence; сильная admin authentication.
- Пятидневный срок является retention floor для принятого evidence. Quota не
  удаляет неистёкшие записи: при pressure включается admission-control/degraded
  health и operator alert. Foreign/unmarked bytes учитываются в общем usage,
  но автоматически не удаляются.

### Границы данных

| Контур | Что хранится | Срок | Что запрещено |
|---|---|---|---|
| `generation_runs`, `generation_events`, artifacts | state, sequence, recovery payload, safe public snapshot, immutable artifacts | product retention | diagnostic, raw provider response, screenshot bytes |
| `model_calls` | provider/model/role, usage, cost, request ID, status | product retention | prompt, response text, provider raw body, secrets |
| forensic volume | redacted prompt/response/tool data, diagnostics, critic/repair evidence, worker attempt metadata, screenshots | terminal `finished_at + 120h` | credentials, auth headers, cookies, unredacted email/phone |
| owner API/SSE | per-event allowlisted projection | response only | request/result/artifact internals, provider raw data, forensic paths |
| admin timeline/export | checksum-verified redacted forensic data plus core timeline | guarded access | model invocation, arbitrary filesystem paths, cross-user owner access |

## Карта новых контрактов

- `builder_lab/generation_events.py` — `GenerationEventType`, полный registry,
  validation/preparation и fail-closed public projection.
- `builder_lab/redaction.py` — рекурсивный bounded redactor для JSON-like
  структур; `redact_diagnostic` остаётся совместимым фасадом.
- `builder_lab/forensics/config.py` — единый env contract для app, worker и
  cleanup.
- `builder_lab/forensics/models.py` — immutable value objects для entry/blob,
  manifest и результатов записи/очистки.
- `builder_lab/forensics/storage.py` — безопасная файловая раскладка, markers,
  canonical JSON, atomic writes, checksums и usage scan.
- `builder_lab/forensics/recorder.py` — best-effort связь event/model hooks с
  файловым storage и SQL manifest metadata.
- `builder_lab/forensics/cleanup.py` — TTL/quota selection и guarded deletion.
- `app/admin/generation_forensics.py` — отдельная capability, search, timeline
  и streaming NDJSON export.
- `migrations/versions/0014_generation_forensics.py` — единый additive schema
  cut для event/forensic metadata и model-call lineage/cost accounting.

## Security gate для отложенного filesystem/admin slice

Следующие задачи разрешено реализовать, но не включать feature flag до
выполнения всех условий:

1. Production admin login требует непустой `ADMIN_PASSWORD` и exact allowlist
   либо подтверждённую OAuth identity с отдельной global-operator capability.
   Allowlisted email при пустом пароле не даёт forensic-доступ.
2. Manifest mutation получает PostgreSQL advisory lock на `run_id` до чтения и
   удерживает его до commit; process-local `asyncio.Lock` является только
   оптимизацией.
3. Cleanup получает отдельный global advisory lock, повторно проверяет terminal
   state/expiry под row lock и сначала атомарно перемещает каталог в `.trash`
   внутри того же root.
4. Удаление выполняется descriptor-relative/no-follow либо только при
   подтверждённом `shutil.rmtree.avoids_symlink_attacks`; parent directory
   fsync выполняется после atomic replace.
5. Missing evidence не удаляет DB manifest молча: создаётся
   `degraded/missing` tombstone с причиной, временем, checksum/expected size.
6. Quota считает marked и foreign bytes. До TTL quota действует как
   admission-control; ранний purge не выдаётся за гарантированное хранение.

## План реализации

### Task 0: Зафиксировать чистую базу исполнения

**Files:**

- Read: `AGENTS.md`
- Read: `docs/GENERATION_FORENSICS_BACKLOG.md`
- Read: `docs/superpowers/specs/2026-07-30-kaigo-verifiable-mvp-design.md`
- Read: `docs/superpowers/plans/2026-07-30-ai-routing-reliability.md`

- [ ] **Step 1: создать изолированный worktree и проверить границы**

Из исходного чистого checkout использовать skill `using-git-worktrees`, создать
ветку `codex/generation-forensics`, затем выполнить:

```powershell
git rev-parse --show-toplevel
git status --short --branch
git diff --check
python -m alembic heads
```

Expected: repo root — `saas-foundation`, рабочее дерево не содержит чужих
изменений, `git diff --check` не печатает ошибок, единственный head —
`0013_pattern_registry`.

- [ ] **Step 2: доказать baseline до первого RED**

```powershell
python -m pytest tests/saas_cases/test_model_router.py tests/saas_cases/test_project_routes.py tests/builder_lab_cases/test_postgres_store.py tests/builder_lab_cases/test_worker.py tests/builder_lab_cases/test_visual_repair_gate.py tests/deployment_cases/test_saas_production_contract.py -q
```

Expected: PASS. Если baseline уже красный, сохранить точный вывод и исправлять
его отдельно; не смешивать прежнюю поломку с forensic-срезом.

### Task 1: Рекурсивная redaction до любой forensic-записи

**Files:**

- Create: `tests/builder_lab_cases/test_generation_forensics_redaction.py`
- Modify: `builder_lab/redaction.py`

- [ ] **Step 1: написать RED-тесты на вложенные секреты и PII**

Зафиксировать тестами следующие случаи:

```python
def test_recursive_redaction_masks_nested_secrets_and_pii():
    value = {
        "headers": {
            "Authorization": "Bearer secret-value-123",
            "Cookie": "session=abc123456789",
        },
        "customer": {
            "email": "person@example.com",
            "phones": ["+7 (927) 000-00-00"],
        },
        "provider": {"raw": "token=secret-value-123"},
    }
    redacted = redact_private_data(value)
    serialized = json.dumps(redacted, ensure_ascii=False)
    assert "secret-value" not in serialized
    assert "person@example.com" not in serialized
    assert "927" not in serialized
    assert redacted["headers"]["Authorization"] == "[REDACTED]"


def test_recursive_redaction_is_bounded_and_does_not_follow_cycles():
    value: list[object] = []
    value.append(value)
    assert redact_private_data(value) == ["[TRUNCATED]"]
```

Добавить отдельные parametrized cases для API-key/JWT/`sk-...`, URL userinfo,
query secrets, `Set-Cookie`, password/client-secret keys, Windows/private Unix
paths, control characters, email и международных телефонов. Проверить, что
ключи и порядок JSON сохраняются, разрешённые числа/bool/null не меняются, а
строки, depth, число элементов и итоговый byte size ограничены.

- [ ] **Step 2: запустить RED**

```powershell
python -m pytest tests/builder_lab_cases/test_generation_forensics_redaction.py -q
```

Expected: FAIL, потому что сейчас существует только плоский
`redact_diagnostic()`.

- [ ] **Step 3: реализовать минимальный recursive contract**

Добавить JSON type aliases и функцию:

```python
def redact_private_data(
    value: object,
    *,
    max_depth: int = 12,
    max_items: int = 1_000,
    max_string_chars: int = 64_000,
) -> JsonValue: ...
```

Правила реализации:

- sensitive key сравнивается case-insensitive после замены `-`/пробела на
  `_`; всё значение такого ключа заменяется на `"[REDACTED]"` без вызова
  `str()`;
- строки проходят существующие regex плюс email/phone/JWT/OpenAI-style token и
  Cookie patterns;
- tuple превращается в JSON array, mapping — в object со строковыми ключами;
- cycle/depth/collection overflow заменяется безопасным marker, а не
  рекурсирует и не попадает в exception text;
- `redact_diagnostic(value, limit=...)` делегирует новому string-redactor и
  сохраняет текущую сигнатуру/результат `str | None`;
- bytes не сериализуются этой функцией: blob API из Task 5 обрабатывает их
  отдельно.

- [ ] **Step 4: запустить GREEN и прежние redaction-тесты**

```powershell
python -m pytest tests/builder_lab_cases/test_generation_forensics_redaction.py tests/builder_lab_cases/test_worker.py -k "redact or diagnostic or forensics" -q
```

Expected: PASS; ни один assert не содержит реальные секреты в failure message.

- [ ] **Step 5: commit**

```powershell
git add builder_lab/redaction.py tests/builder_lab_cases/test_generation_forensics_redaction.py
git commit -m "feat: add recursive forensic redaction"
```

### Task 2: Ввести полный typed registry и public projection

**Files:**

- Create: `builder_lab/generation_events.py`
- Create: `tests/builder_lab_cases/test_generation_event_registry.py`
- Modify: `app/projects/serializers.py`
- Modify: `app/projects/routes.py`
- Modify: `tests/saas_cases/test_project_routes.py`

- [ ] **Step 1: написать RED на полноту registry и fail-closed projection**

В тесте зафиксировать ровно 36 существующих generation types (не funnel
types):

```python
EXPECTED_GENERATION_EVENT_TYPES = {
    "artifact.committed", "artifact.draft_staged", "artifact.seeded",
    "artifact.validated", "direction.failed", "direction.judged",
    "provider.dispatch_armed", "reference.completed", "reference.failed",
    "reference.started", "refinement.started", "repair.completed",
    "repair.started", "run.cancel_requested", "run.cancelled",
    "run.completed", "run.created", "run.failed", "run.terminal_marked",
    "screenshot.captured", "stage.completed", "stage.failed",
    "stage.interrupted", "stage.result_staged", "stage.retry_scheduled",
    "stage.started", "visual_audit.blocked", "visual_audit.completed",
    "visual_audit.passed", "visual_audit.started",
    "visual_critic.completed", "visual_judge.completed",
    "visual_repair.completed", "visual_repair.started",
    "visual_repair.verifier_completed", "visual_repair.verifier_started",
}

def test_registry_is_total_and_contains_no_funnel_events():
    assert {item.value for item in GenerationEventType} == EXPECTED_GENERATION_EVENT_TYPES
    assert set(EVENT_REGISTRY) == set(GenerationEventType)
    assert not ({"run_queued", "free_result", "first_artifact"} & EXPECTED_GENERATION_EVENT_TYPES)


def test_unknown_legacy_event_projects_to_fail_closed_sentinel():
    projected = project_public_generation_event(
        event_type="legacy.secret_event",
        public_message="token=secret-value-123",
        payload={"status": "running", "diagnostic": "secret-value-123"},
    )
    assert projected.event_type == "generation.unknown"
    assert projected.message is None
    assert projected.payload == {}
```

Добавить тест, что известное событие пропускает только поля своего spec, а не
глобального allowlist; вложенные `request`, `result`, `artifact`, `diagnostic`,
`worker_id`, `attempt_id`, provider raw fields и неизвестные keys отсутствуют.
Секрет в разрешённом `message`/поле должен быть повторно redacted.

- [ ] **Step 2: написать route/SSE RED**

В `test_project_routes.py` вставить legacy unknown row напрямую в DB и
проверить оба канала:

- GET run возвращает `type == "generation.unknown"`, `message is None`, пустой
  payload;
- SSE использует `event: generation.unknown`, а не сырое DB значение;
- известный legacy row строится через registry из текущего raw payload.

Defense-in-depth тест stored `public_payload` добавляется в Task 6 после
additive migration; Task 2 не обращается к ещё не существующей ORM-колонке.

- [ ] **Step 3: запустить RED**

```powershell
python -m pytest tests/builder_lab_cases/test_generation_event_registry.py tests/saas_cases/test_project_routes.py -k "event_registry or public_projection or unknown_legacy or sse_unknown" -q
```

Expected: FAIL: нет enum/registry, serializer использует общий
`_PUBLIC_EVENT_PAYLOAD_FIELDS`, SSE печатает raw `event_type`.

- [ ] **Step 4: реализовать registry без изменения DB schema**

Добавить:

```python
class GenerationEventType(StrEnum): ...

@dataclass(frozen=True, slots=True)
class GenerationEventSpec:
    public_fields: frozenset[str]
    stage_result_allowed: bool = False

@dataclass(frozen=True, slots=True)
class PublicGenerationEvent:
    event_type: str
    message: str | None
    payload: dict[str, JsonValue]
```

`EVENT_REGISTRY` обязан быть total mapping. Зафиксировать public fields без
решений «по месту»:

| Event types | Разрешённые поля payload |
|---|---|
| `run.created`, `run.cancel_requested` | `status` |
| `run.cancelled`, `run.terminal_marked` | `status`, `stage`, `error_code` |
| `run.completed` | `status`, `stage`, `revision`, `usage`, `output_refs` |
| `run.failed` | `status`, `stage`, `error_code`, `usage` |
| `stage.started` | `status`, `stage`, `attempt`, `max_executions` |
| `stage.completed` | `status`, `stage`, `next_stage`, `usage`, `output_refs` |
| `stage.failed` | `status`, `stage`, `error_code`, `usage` |
| `stage.interrupted` | `status`, `stage`, `error_code`, `attempt` |
| `stage.result_staged` | `status`, `stage`, `output_refs`, `attempt` |
| `stage.retry_scheduled` | `status`, `stage`, `attempt`, `max_executions`, `not_before`, `supersedes_sequence`, `error_code` |
| `reference.started` | `status`, `stage` |
| `reference.completed` | `status`, `stage`, `usage`, `output_refs` |
| `reference.failed` | `status`, `stage`, `error_code` |
| `refinement.started` | `status`, `stage`, `revision` |
| `repair.started` | `status`, `stage`, `revision`, `attempt` |
| `repair.completed` | `status`, `stage`, `revision`, `usage`, `issues`, `changes`, `output_refs` |
| `artifact.seeded`, `artifact.draft_staged`, `artifact.committed`, `artifact.validated` | `status`, `stage`, `revision`, `issues`, `changes`, `output_refs` |
| `direction.failed` | `status`, `stage`, `error_code`, `usage` |
| `direction.judged` | `status`, `stage`, `revision`, `usage`, `changes`, `output_refs` |
| `provider.dispatch_armed` | `status`, `stage`, `attempt`, `max_executions` |
| `screenshot.captured` | `status`, `stage`, `revision`, `output_refs` |
| `visual_audit.started` | `status`, `stage`, `revision`, `attempt` |
| `visual_audit.completed` | `status`, `stage`, `revision`, `usage`, `issues`, `output_refs`, `error_code` |
| `visual_audit.passed`, `visual_audit.blocked` | `status`, `stage`, `revision`, `issues`, `error_code` |
| `visual_critic.completed`, `visual_judge.completed` | `status`, `stage`, `revision`, `usage`, `issues`, `changes`, `output_refs`, `error_code` |
| `visual_repair.started`, `visual_repair.completed`, `visual_repair.verifier_started`, `visual_repair.verifier_completed` | `status`, `stage`, `revision`, `attempt`, `usage`, `issues`, `changes`, `output_refs`, `error_code` |

Помимо allowlist, projector валидирует форму каждого значения: usage — только
non-negative integer counters, timestamps — bounded ISO-8601 strings, issues и
changes — bounded redacted arrays, `output_refs` — только bounded opaque IDs.
URI, absolute/relative filesystem paths и control characters в `output_refs`
отбрасываются. Provider/model/worker/attempt UUID, path, bytes и checksum всегда
private.

Только `reference.completed` и `repair.completed` получают
`stage_result_allowed=True`. `generation.unknown` — projection sentinel, а не
допустимый persisted enum member.

В `serialize_event()` пока вызывать `project_public_generation_event()` с
`event.event_type`, `event.public_message` и `event.payload`. В SSE header
использовать уже спроецированный `type`. После появления nullable column в Task
4 Task 6 добавит stored `public_payload` как candidate, который всё равно
повторно проверяется registry. Не читать forensic files в owner routes.

- [ ] **Step 5: запустить GREEN**

```powershell
python -m pytest tests/builder_lab_cases/test_generation_event_registry.py tests/saas_cases/test_project_routes.py -k "event or sse" -q
```

Expected: PASS; response snapshots сохраняют нынешние safe поля.

- [ ] **Step 6: commit**

```powershell
git add builder_lab/generation_events.py app/projects/serializers.py app/projects/routes.py tests/builder_lab_cases/test_generation_event_registry.py tests/saas_cases/test_project_routes.py
git commit -m "feat: define generation event registry"
```

### Task 3: Перевести все generation producers на enum

**Files:**

- Modify: `builder_lab/models.py`
- Modify: `builder_lab/store_protocol.py`
- Modify: `builder_lab/store.py`
- Modify: `builder_lab/orchestrator.py`
- Modify: `builder_lab/postgres_store.py`
- Modify: `builder_lab/visual_gate.py`
- Modify: `builder_lab/worker.py`
- Modify: `app/projects/routes.py`
- Modify: `tests/builder_lab_cases/test_generation_event_registry.py`
- Modify: `tests/builder_lab_cases/test_orchestrator.py`
- Modify: `tests/builder_lab_cases/test_postgres_store.py`
- Modify: `tests/builder_lab_cases/test_worker.py`
- Modify: `tests/builder_lab_cases/test_visual_repair_gate.py`

- [ ] **Step 1: написать RED на runtime и static producer boundary**

Проверить:

```python
def test_new_generation_event_rejects_raw_string_type():
    with pytest.raises(TypeError, match="GenerationEventType"):
        prepare_generation_event(event_type="run.created", message="queued", payload={})


def test_stage_result_allowlist_comes_from_registry():
    assert STAGE_RESULT_EVENT_TYPES == {
        GenerationEventType.REFERENCE_COMPLETED,
        GenerationEventType.REPAIR_COMPLETED,
    }
```

Добавить AST regression test для production paths выше: keyword
`event_type="..."` запрещён в вызовах `append_event`, `finish`,
`append_attempt_event`, `_append_record`, `_append_event` и
`GenerationEvent(...)`. Явно исключить вызовы `record_funnel_event`, чтобы
funnel taxonomy осталась строковой и независимой.

- [ ] **Step 2: запустить RED**

```powershell
python -m pytest tests/builder_lab_cases/test_generation_event_registry.py -k "rejects_raw_string or producer or stage_result" -q
```

Expected: FAIL на текущих строковых producer callsites.

- [ ] **Step 3: заменить типы и callsites минимально**

- `BuilderEvent.event_type`, `RunStoreProtocol.append_event/finish`,
  `StageResult` validation и worker queue methods принимают
  `GenerationEventType`;
- все generation callsites используют enum constants, а SQL filters и DB
  serialization — `.value`;
- `BuilderEvent.to_dict()` сохраняет wire format string через `.value`;
- `BuilderEvent.from_dict()` и DB conversion принимают только известные типы;
  fail-closed legacy handling остаётся в owner serializer из Task 2;
- `ALLOWED_STAGE_RESULT_EVENTS` удалить и получить allowed set из registry;
- funnel callsites не трогать.

- [ ] **Step 4: запустить GREEN и существующую orchestration regression suite**

```powershell
python -m pytest tests/builder_lab_cases/test_generation_event_registry.py tests/builder_lab_cases/test_orchestrator.py tests/builder_lab_cases/test_postgres_store.py tests/builder_lab_cases/test_worker.py tests/builder_lab_cases/test_visual_repair_gate.py -q
```

Expected: PASS; wire JSON по-прежнему содержит строковый `type`.

- [ ] **Step 5: вручную проверить отсутствие обходных writers**

```powershell
rg -n "GenerationEvent\(" app builder_lab
rg -n 'event_type="' app/projects/routes.py builder_lab
```

Expected: три фактические DB persistence seams известны и используют общий
preparation contract; оставшиеся строковые hits относятся только к
`record_funnel_event` или test fixtures.

- [ ] **Step 6: commit**

```powershell
git add builder_lab/models.py builder_lab/store_protocol.py builder_lab/store.py builder_lab/orchestrator.py builder_lab/postgres_store.py builder_lab/visual_gate.py builder_lab/worker.py app/projects/routes.py tests/builder_lab_cases/test_generation_event_registry.py tests/builder_lab_cases/test_orchestrator.py tests/builder_lab_cases/test_postgres_store.py tests/builder_lab_cases/test_worker.py tests/builder_lab_cases/test_visual_repair_gate.py
git commit -m "refactor: type all generation event producers"
```

### Task 4: Добавить единый additive schema `0014_generation_forensics`

Эта задача выполняется одним schema-owner и включает contracts из
`2026-07-30-model-call-lineage-costs.md`: `generation_stage_attempts`, composite
run/attempt membership, model-call lineage/cache/cost fields, conservative
legacy backfill и named constraints. Отдельная неполная версия `0014` не
коммитится и не получает промежуточный fingerprint.

**Files:**

- Create: `migrations/versions/0014_generation_forensics.py`
- Create: `tests/saas_cases/test_generation_forensics_migration.py`
- Modify: `app/saas/models.py`
- Modify: `tests/saas_cases/test_schema.py`
- Modify: `tests/saas_cases/test_project_recovery_migration.py`
- Modify: `tests/saas_cases/test_publication_migration.py`
- Modify: `tests/saas_cases/test_billing_migration.py`
- Modify: `tests/deployment_cases/test_saas_production_contract.py`
- Modify: `scripts/preflight_saas_schema.py`

- [ ] **Step 1: написать schema/migration RED**

Проверить ORM metadata и migration module:

```python
def test_generation_forensics_migration_identity():
    migration = importlib.import_module(
        "migrations.versions.0014_generation_forensics"
    )
    assert migration.revision == "0014_generation_forensics"
    assert migration.down_revision == "0013_pattern_registry"


def test_generation_event_forensic_columns_are_additive_and_nullable():
    events = Base.metadata.tables["generation_events"]
    assert events.c.registry_version.nullable
    assert events.c.public_payload.nullable
    assert events.c.forensic_ref.nullable
```

PostgreSQL test должен создать row на revision `0013`, выполнить upgrade head и
доказать, что старые `event_type`, `public_message` и `payload` не изменились, а
три новых колонки равны `NULL`. Отдельно проверить downgrade до `0013` и повторный
upgrade. Не выполнять этот test на production DB.

- [ ] **Step 2: запустить RED**

```powershell
python -m pytest tests/saas_cases/test_generation_forensics_migration.py tests/saas_cases/test_schema.py tests/deployment_cases/test_saas_production_contract.py -k "forensic or alembic_has_one_production_head" -q
```

Expected: FAIL: migration/tables/columns отсутствуют, head ещё `0013`.

- [ ] **Step 3: реализовать только additive ORM/schema**

В `GenerationEvent` добавить nullable:

- `registry_version: Integer`;
- `public_payload: _json_document()` в ORM и `postgresql.JSONB()` в migration;
- `forensic_ref: String(512)` — только POSIX-style относительный storage key,
  никогда absolute host/container path.

Добавить `GenerationForensicManifest` (`generation_forensic_manifests`):

- UUID `id`, unique/cascade `run_id`, indexed `user_id` и `project_id`;
- unique `storage_key`, positive `schema_version`;
- `state` из `pending|active|completed|failed|cancelled|degraded`;
- non-negative `last_event_sequence`, `entry_count`, `byte_count`;
- nullable 64-char `manifest_sha256`, nullable timezone-aware `expires_at`;
- JSON metadata columns используют `_json_document()`/PostgreSQL JSONB, не
  строковый JSON;
- `created_at`, `updated_at` и indexes `(user_id, created_at)`,
  `(state, expires_at)`.

Добавить `GenerationForensicAccessLog` (`generation_forensic_access_logs`):

- bigint `id`, normalized `actor_email`, `action` из
  `search|view|export`, `allowed`;
- nullable `run_id`, `project_id`, `user_id` с `ON DELETE SET NULL`;
- redacted JSONB `metadata`, timezone-aware `created_at`;
- indexes `(run_id, created_at)` и `(actor_email, created_at)`.

Закрепить перечисленные state/action множества и non-negative/positive правила
DB `CheckConstraint`-ами: прямой SQL insert не должен обходить Python validation.

Migration не backfill-ит старые events и не удаляет/переименовывает ни одного
поля. Downgrade удаляет только новые indexes/tables/columns в обратном порядке.

- [ ] **Step 4: обновить все head assertions и schema fingerprint**

В migration tests, которые проверяют текущий head, заменить только ожидаемый
head на `0014_generation_forensics`; исторический
`test_pattern_registry_migration.py` оставить проверять собственный revision
`0013`.

На одноразовой PostgreSQL базе:

```powershell
$env:DATABASE_URL = $env:KAIGO_TEST_POSTGRES_URL
python -m alembic upgrade head
python -c "import os; from scripts.preflight_saas_schema import inspect_schema, _schema_fingerprint; print(_schema_fingerprint(inspect_schema(os.environ['KAIGO_TEST_POSTGRES_URL'])))"
```

Перенести напечатанное точное значение в
`EXPECTED_VERSIONED_SCHEMA_FINGERPRINTS["0014_generation_forensics"]`. Не
копировать fingerprint от `0013` и не ослаблять fail-closed classifier.

- [ ] **Step 5: запустить GREEN на disposable PostgreSQL**

```powershell
python -m pytest tests/saas_cases/test_generation_forensics_migration.py tests/saas_cases/test_schema.py tests/saas_cases/test_project_recovery_migration.py tests/saas_cases/test_publication_migration.py tests/saas_cases/test_billing_migration.py tests/deployment_cases/test_saas_production_contract.py -q
python scripts/preflight_saas_schema.py --database-url $env:KAIGO_TEST_POSTGRES_URL
```

Expected: PASS, preflight сообщает exact known head. Если
`KAIGO_TEST_POSTGRES_URL` не указывает на disposable PostgreSQL, это blocker для
завершения Task 4, а не повод пропустить migration proof.

- [ ] **Step 6: commit**

```powershell
git add migrations/versions/0014_generation_forensics.py app/saas/models.py scripts/preflight_saas_schema.py tests/saas_cases/test_generation_forensics_migration.py tests/saas_cases/test_schema.py tests/saas_cases/test_project_recovery_migration.py tests/saas_cases/test_publication_migration.py tests/saas_cases/test_billing_migration.py tests/deployment_cases/test_saas_production_contract.py
git commit -m "feat: add generation forensic schema"
```

### Task 5: Реализовать config и безопасный persistent storage

**Files:**

- Create: `builder_lab/forensics/__init__.py`
- Create: `builder_lab/forensics/config.py`
- Create: `builder_lab/forensics/models.py`
- Create: `builder_lab/forensics/storage.py`
- Create: `tests/builder_lab_cases/test_generation_forensics_storage.py`
- Modify: `app/config.py`
- Modify: `builder_lab/config.py`
- Modify: `tests/saas_cases/test_config.py`
- Modify: `tests/builder_lab_cases/test_config.py`

- [ ] **Step 1: написать config RED**

Зафиксировать env contract:

```text
KAIGO_GENERATION_FORENSICS_ENABLED=false
KAIGO_GENERATION_FORENSICS_ROOT=/app/data/generation-forensics
KAIGO_GENERATION_FORENSICS_TTL_HOURS=120
KAIGO_GENERATION_FORENSICS_MAX_BYTES=10737418240
KAIGO_GENERATION_FORENSICS_ADMIN_EMAILS=
```

Tests:

- default disabled и TTL ровно 120h;
- development/test принимает только 72..120h;
- production + enabled требует absolute root, TTL 120 и непустой normalized
  admin allowlist;
- disabled config не создаёт каталог;
- `repr(config)` не содержит email list или filesystem secrets.

- [ ] **Step 2: написать storage RED**

Проверить layout и инварианты:

```text
<root>/.kaigo-generation-forensics-root.json
<root>/runs/<first-two-run-hex>/<run-uuid>/.kaigo-generation-run.json
<root>/runs/<first-two-run-hex>/<run-uuid>/manifest.json
<root>/runs/<first-two-run-hex>/<run-uuid>/events/00000001-run.created.json
<root>/runs/<first-two-run-hex>/<run-uuid>/model-calls/<call-id>-01.json
<root>/runs/<first-two-run-hex>/<run-uuid>/blobs/<sha256>.jpg
```

Tests должны доказать:

- root/run marker содержит exact `kind`, `schema_version`, user/project/run IDs;
- read-only open mode не создаёт root/marker и безопасно сообщает
  unavailable/missing, тогда как writable mode отказывается принимать чужой
  непустой root без Kaigo marker;
- entry/blob immutable, mode `0600`, directories `0700`, manifest заменяется
  atomарно canonical UTF-8 JSON;
- каждый manifest entry содержит relative path, byte count и SHA-256; DB-side
  digest считается по canonical bytes всего `manifest.json`;
- повторное открытие нового `GenerationForensicStorage` на том же root читает и
  проверяет manifest — это process-restart proof;
- absolute path, `..`, drive prefix, symlink в root/run/entry и чужой marker
  отвергаются до чтения/записи;
- screenshot принимает только `image/jpeg`, сверяет заявленные
  `byte_count/sha256`, не перезаписывает существующий blob;
- oversized entry/blob возвращает bounded failure без записи partial/temp file;
- `usage()` считает actual bytes только внутри валидных marked run dirs.

- [ ] **Step 3: запустить RED**

```powershell
python -m pytest tests/saas_cases/test_config.py tests/builder_lab_cases/test_config.py tests/builder_lab_cases/test_generation_forensics_storage.py -q
```

Expected: FAIL: shared config/package/storage отсутствуют.

- [ ] **Step 4: реализовать config/value objects/storage**

`GenerationForensicsConfig.from_env(environment=...)` использовать и в
`AppConfig`, и в `BuilderLabConfig`; не дублировать parsing. В value objects
ввести `ForensicBlob`, `ForensicEntry`, `ForensicManifest` и
`ForensicWriteResult` с UUID/timestamp/checksum validation.

Для immutable files переиспользовать
`builder_lab.reference_storage.private_write_new`; для `manifest.json` сделать
отдельный same-directory temp + `fsync` + `os.replace`, проверяя все parents на
symlink до и после resolve. Manifest не содержит absolute path, env или secret.
JSON payload всегда проходит `redact_private_data()` перед encoding; blob bytes
никогда не проходят через `str()` и не попадают в logs.

- [ ] **Step 5: запустить GREEN**

```powershell
python -m pytest tests/saas_cases/test_config.py tests/builder_lab_cases/test_config.py tests/builder_lab_cases/test_generation_forensics_storage.py tests/builder_lab_cases/test_reference_security.py -q
```

Expected: PASS; reference-evidence behavior не изменился.

- [ ] **Step 6: commit**

```powershell
git add builder_lab/forensics app/config.py builder_lab/config.py tests/saas_cases/test_config.py tests/builder_lab_cases/test_config.py tests/builder_lab_cases/test_generation_forensics_storage.py
git commit -m "feat: add private generation forensic storage"
```

### Task 6: Подключить event persistence, manifest lifecycle и screenshots

**Files:**

- Create: `builder_lab/forensics/recorder.py`
- Modify: `builder_lab/generation_events.py`
- Modify: `builder_lab/store_protocol.py`
- Modify: `builder_lab/store.py`
- Modify: `builder_lab/postgres_store.py`
- Modify: `builder_lab/worker.py`
- Modify: `builder_lab/visual_gate.py`
- Modify: `app/projects/routes.py`
- Modify: `app/server.py`
- Modify: `scripts/run_builder_worker.py`
- Modify: `tests/builder_lab_cases/test_postgres_store.py`
- Modify: `tests/builder_lab_cases/test_worker.py`
- Modify: `tests/builder_lab_cases/test_visual_repair_gate.py`
- Modify: `tests/saas_cases/test_project_routes.py`

- [ ] **Step 1: написать RED на три DB writer seams**

Для direct route writer, `PostgresRunStore._append_record` и
`PostgresWorkerQueue._append_event` проверить один contract:

- `registry_version == 1`;
- `public_payload` равен per-type projection;
- operational `payload` остаётся достаточным для текущего durable recovery, но
  не содержит `diagnostic`, screenshot bytes или явно переданные
  `forensic_payload` fields;
- `forensic_ref` — relative entry path только после успешной записи;
- неизвестный тип нельзя сохранить новым writer-ом;
- legacy rows из Task 2 остаются читаемыми.

Добавить defense-in-depth case, отложенный из Task 2: если новый row имеет
stored `public_payload` с разрешённым и внедрённым лишним/секретным полем,
serializer повторно прогоняет candidate через spec и отдаёт только разрешённое.

Добавить test, который monkeypatch-ит storage write exception: core event и
sequence обязаны сохраниться, `forensic_ref` остаётся `NULL`, manifest получает
`state="degraded"`, а exception/log не содержит исходный private payload.

Добавить concurrent test: `asyncio.gather()` пишет не менее 20 event/model
entries одного run, после повторного открытия manifest содержит все 20 unique
entries, корректные counts/checksums и не содержит потерянного update.

- [ ] **Step 2: написать RED на lifecycle и screenshot evidence**

Проверить:

- при enqueue enabled-режим создаёт SQL manifest `pending` с deterministic
  storage key, но web app не обязан писать на volume;
- worker перед первым model/stage call materialизует run marker/manifest и
  переводит row в `active`;
- `completed`, `failed`, `cancelled` устанавливают соответственно state и exact
  `expires_at = run.finished_at + timedelta(hours=120)`; active/pending run имеет
  `expires_at IS NULL`;
- повторный terminal hook idempotent и не сдвигает expiry;
- `screenshot.captured` передаёт `CapturedScreenshot.data` как typed
  `ForensicBlob`, а public payload содержит только opaque `output_refs`;
- critic/judge/repair diagnostics уходят в `forensic_payload`, не в DB payload;
- in-memory store принимает новые optional args, но не сохраняет private bytes.

- [ ] **Step 3: запустить RED**

```powershell
python -m pytest tests/builder_lab_cases/test_postgres_store.py tests/builder_lab_cases/test_worker.py tests/builder_lab_cases/test_visual_repair_gate.py tests/saas_cases/test_project_routes.py -k "forensic or public_payload or screenshot or diagnostic" -q
```

Expected: FAIL: новые columns не заполняются, recorder и blob arguments
отсутствуют.

- [ ] **Step 4: реализовать единый preparation/recording flow**

`prepare_generation_event(...)` возвращает immutable object с:

```python
event_type: GenerationEventType
public_message: str | None
operational_payload: dict[str, JsonValue]
public_payload: dict[str, JsonValue]
forensic_payload: dict[str, JsonValue]
```

Все три persistence seams обязаны вызвать этот helper до
`GenerationEvent(...)`; не копировать projection/redaction logic между ними.
Расширить store API optional параметрами
`forensic_payload: Mapping[str, object] | None = None` и
`forensic_blobs: tuple[ForensicBlob, ...] = ()`.

В preparation явно разделить policy: `public_message`, public candidate,
operational payload и private JSON проходят bounded recursive sanitizer до
своей persistence boundary; registry-specific extraction не должна оставлять
`diagnostic`/forensic fields в operational DB payload. После redaction снова
проверить JSON byte limits, чтобы replacement markers не обходили лимит.

`GenerationForensicRecorder` выполняет filesystem I/O через
`asyncio.to_thread`, затем обновляет SQL manifest metadata в coroutine. Ошибка
файла не откатывает core generation transaction; она возвращает bounded
`ForensicWriteResult(degraded=True)`. При этом private payload не имеет fallback
в DB или обычный log.

Recorder использует process-local `asyncio.Lock` только как оптимизацию, а
межпроцессную корректность обеспечивает PostgreSQL advisory lock per run. Один
production run одновременно принадлежит только одному leased worker. SQL
manifest update дополнительно берёт row `FOR UPDATE`, а каждый новый write
reconciles counts/digest из canonical file manifest, чтобы crash между
filesystem и DB cache не терял уже записанное evidence.

Сохранять в event evidence redacted diagnostic, worker ID, lease attempt ID,
stage attempt metadata, critic/judge/repair structures и ссылки на blobs.
`stage.result_staged.result`, `run.created.request` и artifact recovery fields
пока остаются операционным state: не переносить их на volume и не ломать
restart recovery. Admin/export повторно redacts эти legacy/internal structures
на чтении.

`app/server.py` передаёт `cfg.generation_forensics` в
`setup_project_routes(...)`; аргумент route setup имеет disabled default для
тестов/legacy callers. Route создаёт только SQL row `pending` и deterministic
storage key. Файловый root впервые материализует worker с UID 10001, поэтому
internet-facing app не получает write requirement к private volume.

- [ ] **Step 5: запустить GREEN и durable restart regression**

```powershell
python -m pytest tests/builder_lab_cases/test_postgres_store.py tests/builder_lab_cases/test_worker.py tests/builder_lab_cases/test_visual_repair_gate.py tests/saas_cases/test_project_routes.py -q
```

Expected: PASS; существующие stage replay/retry/lease tests по-прежнему читают
операционный payload после создания нового store/queue instance.

- [ ] **Step 6: commit**

```powershell
git add builder_lab/forensics/recorder.py builder_lab/generation_events.py builder_lab/store_protocol.py builder_lab/store.py builder_lab/postgres_store.py builder_lab/worker.py builder_lab/visual_gate.py app/projects/routes.py app/server.py scripts/run_builder_worker.py tests/builder_lab_cases/test_postgres_store.py tests/builder_lab_cases/test_worker.py tests/builder_lab_cases/test_visual_repair_gate.py tests/saas_cases/test_project_routes.py
git commit -m "feat: persist generation event forensics"
```

### Task 7: Сохранять redacted model attempts без изменения accounting

**Files:**

- Modify: `app/models/router.py`
- Modify: `scripts/run_builder_worker.py`
- Modify: `builder_lab/forensics/recorder.py`
- Modify: `tests/saas_cases/test_model_router.py`
- Modify: `tests/builder_lab_cases/test_worker.py`

- [ ] **Step 1: написать RED на success/error/fallback/cancel**

Добавить fake `ModelForensicRecorder` и проверить для каждого provider attempt:

- request entry создаётся с тем же `call_id`, run ID, provider/model/role/mode,
  prompt version и attempt, что и `ModelCallAuditRecord`;
- success entry содержит redacted `response.text`, `parsed`, `raw`, usage и
  request ID;
- provider error/timeout/cancel entry содержит только redacted bounded error и
  корректный final status;
- fallback создаёт две разные attempt entries и не создаёт новый
  `GenerationEvent.sequence`;
- input image bytes не пишутся, но сохраняются count/size/SHA-256;
- recorder exception не меняет provider result/error taxonomy и не мешает
  `SqlModelCallAudit` финализировать row;
- ни `ModelCall`, ни captured logs не содержат prompt/response/token fixture.

- [ ] **Step 2: запустить RED**

```powershell
python -m pytest tests/saas_cases/test_model_router.py -k "forensic" -q
```

Expected: FAIL: `ModelRouter` не принимает forensic recorder.

- [ ] **Step 3: реализовать отдельный optional hook**

Добавить protocol с no-op default, например:

```python
class ModelForensicRecorder(Protocol):
    async def record_request(...): ...
    async def record_response(...): ...
    async def record_error(...): ...
```

Hook вызывается рядом с существующим audit, используя уже созданный `call_id`.
Сначала redaction, затем filesystem write; helper `*_safe` ловит recorder
failure отдельно от provider call. Не менять `SqlModelCallAudit` и его правило
«provider accounting only».

В `scripts/run_builder_worker.py::run()` создать один recorder из того же
`GenerationForensicsConfig`, передать его в `PostgresWorkerQueue` и
`make_runtime_model_router(...)`. Worker claim materialизует manifest до первого
hook. При feature off используется no-op без filesystem side effects.

- [ ] **Step 4: запустить GREEN**

```powershell
python -m pytest tests/saas_cases/test_model_router.py tests/builder_lab_cases/test_worker.py -k "model or forensic or fallback or timeout" -q
```

Expected: PASS; существующее model-call accounting не изменилось.

- [ ] **Step 5: commit**

```powershell
git add app/models/router.py scripts/run_builder_worker.py builder_lab/forensics/recorder.py tests/saas_cases/test_model_router.py tests/builder_lab_cases/test_worker.py
git commit -m "feat: record model attempt forensics"
```

### Task 8: Добавить exact TTL и quota cleanup

**Files:**

- Create: `builder_lab/forensics/cleanup.py`
- Create: `scripts/cleanup_generation_forensics.py`
- Create: `tests/builder_lab_cases/test_generation_forensics_cleanup.py`
- Modify: `builder_lab/forensics/models.py`

- [ ] **Step 1: написать RED на точную TTL-границу**

С frozen `now` проверить completed/failed/cancelled manifests:

- за одну микросекунду до `expires_at` каталог и row остаются;
- ровно в `expires_at` валидный marked каталог удаляется;
- active/pending/null-expiry никогда не удаляется;
- core `generation_runs`, `generation_events`, `generation_artifacts` и
  `model_calls` остаются после cleanup;
- missing directory оставляет `degraded/missing` tombstone; manifest row не
  удаляется молча;
- filesystem delete failure оставляет DB row для retry.

- [ ] **Step 2: написать RED на marker/path/quota safety**

Проверить, что cleanup:

- прекращает работу с non-zero result, если root marker отсутствует/неверен;
- игнорирует foreign directory, malformed/oversized marker, symlink и marker с
  несовпадающим run ID;
- сначала удаляет все expired terminal runs, затем при
  `actual_bytes > max_total_bytes` — самые старые terminal runs, даже если они
  ещё не expired; active runs не кандидаты;
- сортирует quota candidates детерминированно по `finished_at`, затем run UUID;
- `--dry-run` не меняет files/DB;
- JSON summary содержит `bytes_before`, `bytes_after`, `expired_removed`,
  `quota_removed`, `foreign_skipped`, `degraded` и timestamp, но не private
  payload/path вне storage key.

- [ ] **Step 3: запустить RED**

```powershell
python -m pytest tests/builder_lab_cases/test_generation_forensics_cleanup.py -q
```

Expected: FAIL: cleanup module/CLI отсутствуют.

- [ ] **Step 4: реализовать guarded двухфазную очистку**

Алгоритм:

1. Проверить root marker и canonical root.
2. Получить manifest candidates из DB; не выводить private content.
3. Для каждого кандидата повторно проверить relative path, run marker, symlink
   и containment.
4. Удалить filesystem directory; только после успеха удалить SQL manifest row.
5. Пересчитать marked и foreign actual usage. Quota pressure до истечения TTL
   включает admission-control/degraded health; неистёкшие evidence не удалять.
6. Commit DB changes и вывести одну JSON summary line.

CLI принимает `--database-url`, `--root`, `--now` только для тестов/операторской
диагностики и `--dry-run`. По умолчанию читает общий env config. Naive datetime,
относительный production root и TTL вне policy должны завершать команду до
mutation.

- [ ] **Step 5: запустить GREEN**

```powershell
python -m pytest tests/builder_lab_cases/test_generation_forensics_cleanup.py tests/builder_lab_cases/test_generation_forensics_storage.py -q
python scripts/cleanup_generation_forensics.py --help
```

Expected: PASS; help не требует DATABASE_URL и не создаёт root.

- [ ] **Step 6: commit**

```powershell
git add builder_lab/forensics/cleanup.py builder_lab/forensics/models.py scripts/cleanup_generation_forensics.py tests/builder_lab_cases/test_generation_forensics_cleanup.py
git commit -m "feat: enforce forensic ttl and quota"
```

### Task 9: Реализовать fail-closed admin timeline/search/export

**Files:**

- Create: `app/admin/generation_forensics.py`
- Create: `tests/saas_cases/test_generation_forensics_admin.py`
- Modify: `app/admin/auth.py`
- Modify: `app/admin/routes.py`
- Modify: `app/admin/tenants.py`
- Modify: `app/server.py`
- Modify: `tests/saas_cases/test_project_routes.py`

- [ ] **Step 1: написать authorization/search RED**

С `aiohttp` TestClient и `SimpleCookieStorage` проверить:

- disabled feature получает 404 без storage/DB side effects; при enabled
  feature пустой `KAIGO_GENERATION_FORENSICS_ADMIN_EMAILS` или authenticated
  legacy admin вне списка тоже получает 404, не 403 и не пустую страницу;
- email сравнивается normalized exact membership, не suffix/substring;
- при enabled feature известная authenticated session с denied access создаёт
  `GenerationForensicAccessLog(allowed=False)` без query/private payload;
- member получает доступ; успешные search/view/export записываются с
  `allowed=True`;
- `/admin/generation-runs` без фильтра показывает только форму, а exact
  `run_id=<uuid>` или `user_id=<int>` возвращает не более 100 записей;
- invalid UUID/int даёт 400 без SQL injection или filesystem lookup.

- [ ] **Step 2: написать timeline/export RED**

Для `/admin/generation-runs/{run_id}` и
`/admin/generation-runs/{run_id}/export.ndjson` проверить:

- DB events идут строго по `sequence`; model calls/evidence присоединены по
  timestamp/call ID и не меняют operational order;
- page показывает state, project/user/run IDs, provider/model/role, usage/cost,
  request ID, expiry, byte count, checksum status и degraded/missing evidence;
- HTML экранирует `<script>` из legacy DB и private entry;
- storage читает только manifest-listed relative paths и проверяет SHA-256 до
  отображения; checksum mismatch показывается как unavailable и не отдаёт bytes;
- export — streaming `application/x-ndjson`, `Content-Disposition: attachment`,
  `Cache-Control: no-store`; первая строка — run metadata, затем ordered event,
  model-call и evidence records, binary content — bounded base64 records;
- весь exported JSON повторно проходит redaction на чтении, поэтому legacy DB
  secret не утекает;
- fake provider spy имеет zero calls: timeline/export не обращается к модели;
- обычный owner run API другого tenant/user остаётся 404 и никогда не содержит
  `forensic_ref`, prompt, provider response или admin URLs.

- [ ] **Step 3: запустить RED**

```powershell
python -m pytest tests/saas_cases/test_generation_forensics_admin.py tests/saas_cases/test_project_routes.py -k "forensic or cross_user" -q
```

Expected: FAIL: routes/capability отсутствуют.

- [ ] **Step 4: реализовать отдельную capability и read model**

Не переиспользовать permissive поведение `settings.ADMIN_EMAILS == empty`.
Forensic guard использует только
`AppConfig.generation_forensics.admin_emails`; при disabled/empty/non-member
делает 404. Login/session остаётся существующим, но право просмотра отдельное.

Текущий одинаковый private `_require_session` уже продублирован в
`app/admin/routes.py` и `app/admin/tenants.py`. Вынести его без изменения
поведения в публичный `require_admin_session()` в `app/admin/auth.py` и
переиспользовать во всех трёх admin modules; не добавлять третью копию guard.

`setup_generation_forensics_routes(...)` зарегистрировать через
`setup_admin_routes`, а `app/server.py` передаёт typed config и read-only
storage open mode, который никогда не создаёт/исправляет marker. Query строится
через SQLAlchemy typed filters. Ни URL, ни query не
принимают storage path. Manifest/access logging errors fail closed для admin
response, но не раскрывают host path.

Страницы используют существующий `render_layout(..., nav_extra=...)`; link на
forensic search добавляется только после успешной capability check. Весь
динамический HTML проходит `html.escape`, не добавлять raw CSS/JS из evidence.

Timeline строится из core DB rows плюс checksum-verified manifest entries.
ModelCall metadata берётся из `model_calls`; private model entry только
дополняет его redacted input/output. Export stream читает по одному entry/blob,
не собирает весь run в RAM.

- [ ] **Step 5: запустить GREEN**

```powershell
python -m pytest tests/saas_cases/test_generation_forensics_admin.py tests/saas_cases/test_project_routes.py -q
```

Expected: PASS; access log содержит allow/deny audit, owner API не расширен.

- [ ] **Step 6: commit**

```powershell
git add app/admin/generation_forensics.py app/admin/auth.py app/admin/routes.py app/admin/tenants.py app/server.py tests/saas_cases/test_generation_forensics_admin.py tests/saas_cases/test_project_routes.py
git commit -m "feat: add guarded generation forensic timeline"
```

### Task 10: Подключить persistent volume и hourly timer

**Files:**

- Create: `deploy/systemd/kaigo-generation-forensics-cleanup.service`
- Create: `deploy/systemd/kaigo-generation-forensics-cleanup.timer`
- Create: `scripts/install_generation_forensics_cleanup_timer.sh`
- Create: `scripts/smoke_generation_forensics.py`
- Create: `tests/deployment_cases/test_generation_forensics_deployment.py`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `docs/SAAS_PRODUCTION_RUNBOOK.md`
- Modify: `tests/deployment_cases/test_saas_production_contract.py`

- [ ] **Step 1: написать deployment contract RED**

Проверить parsed compose/unit/script текстом:

- app получает тот же host directory в
  `/app/data/generation-forensics:ro`;
- `builder-worker` получает его `rw`, сохраняет `read_only: true`, UID/GID 10001
  и существующий tmpfs `/app/data/builder-evidence`;
- новый operations service использует app image, DB network и тот же mount
  `rw`, запускает только `python scripts/cleanup_generation_forensics.py`;
- никакой service не использует anonymous/tmpfs volume для нового path;
- systemd service имеет `WorkingDirectory=/opt/kaigo/current`, запускает
  `docker compose --profile operations run --rm --no-deps
  generation-forensics-cleanup`;
- timer имеет `OnUnitActiveSec=1h`, `Persistent=true`, randomized delay и связан
  только с новым service;
- installer требует root, создаёт
  `/var/lib/kaigo/generation-forensics` владельцем `10001:10001`, mode `0700`,
  копирует units, делает daemon-reload, one-shot smoke и только затем enable;
- старые reference cleanup files не меняются.

- [ ] **Step 2: запустить RED**

```powershell
python -m pytest tests/deployment_cases/test_generation_forensics_deployment.py tests/deployment_cases/test_saas_production_contract.py -q
```

Expected: FAIL: volume/service/timer отсутствуют.

- [ ] **Step 3: реализовать compose/env/systemd contract**

Добавить bind mount:

```yaml
${KAIGO_GENERATION_FORENSICS_HOST_DIR:-./data/generation-forensics}:/app/data/generation-forensics
```

и передать всем трём нужным services общий env contract из Task 5. App mount
явно `:ro`, worker/cleanup — `:rw`. Не удалять parent `./data:/app/data` и
существующий reference tmpfs; dedicated child mount должен иметь более узкий
path.

`generation-forensics-cleanup` находится в profile `operations`, имеет
`restart: "no"`, зависит от healthy DB и не публикует ports. Unit использует
`COMPOSE_PROJECT_NAME=kaigo` и `/etc/kaigo/release.env` для immutable app image;
credentials остаются в существующем compose `.env`, а не в unit/repo.

В runbook добавить exact enable/disable/dry-run/status/space/export procedures,
предупреждение о 5-day private retention и rollback: сначала выключить feature
flag/timer, сохранить volume, и только потом откатывать app; downgrade migration
не запускать, пока manifest rows нужны для расследования.

- [ ] **Step 4: добавить token-free persistence smoke**

`scripts/smoke_generation_forensics.py` должен иметь команды `seed`, `verify` и
`mark-expired`, работать только с synthetic UUID/redacted fixture и не вызывать
provider. С двумя разными одноразовыми worker containers проверить, что второй
видит checksum после удаления первого:

```powershell
$env:KAIGO_GENERATION_FORENSICS_HOST_DIR = (Join-Path $env:TEMP "kaigo-forensics-smoke-$PID")
New-Item -ItemType Directory -Force -Path $env:KAIGO_GENERATION_FORENSICS_HOST_DIR | Out-Null
docker compose --profile saas-worker run --rm --no-deps builder-worker python scripts/smoke_generation_forensics.py seed --root /app/data/generation-forensics
docker compose --profile saas-worker run --rm --no-deps builder-worker python scripts/smoke_generation_forensics.py verify --root /app/data/generation-forensics
```

Expected: `verify` печатает одну JSON line с `checksum_valid=true`. Это
проверяет container recreation и bind persistence без DB и токенов. Cleanup
dry-run отдельно проверяется против disposable PostgreSQL в Task 8 и на
production host только по runbook. Удалять disposable host directory только
после `Resolve-Path` и проверки, что он находится под `$env:TEMP` и имя
начинается с `kaigo-forensics-smoke-`.

- [ ] **Step 5: запустить GREEN**

```powershell
docker compose config --quiet
python -m pytest tests/deployment_cases/test_generation_forensics_deployment.py tests/deployment_cases/test_saas_production_contract.py -q
bash -n scripts/install_generation_forensics_cleanup_timer.sh
```

Expected: PASS. Не выполнять `systemctl enable` на реальном host в рамках
implementation task без отдельного разрешения.

- [ ] **Step 6: commit**

```powershell
git add .env.example docker-compose.yml deploy/systemd/kaigo-generation-forensics-cleanup.service deploy/systemd/kaigo-generation-forensics-cleanup.timer scripts/install_generation_forensics_cleanup_timer.sh scripts/smoke_generation_forensics.py docs/SAAS_PRODUCTION_RUNBOOK.md tests/deployment_cases/test_generation_forensics_deployment.py tests/deployment_cases/test_saas_production_contract.py
git commit -m "ops: deploy five day generation forensics"
```

### Task 11: End-to-end acceptance, редакторский контур и review

**Files:**

- Modify: `docs/GENERATION_FORENSICS_BACKLOG.md`
- Modify: `docs/product-journal/2026-07.md`
- Create: `docs/telegram/release-packets/2026-07-30-generation-forensics.md`
- Modify: `docs/telegram/content-backlog.md`
- Modify if evidence exists: `docs/telegram/assets/README.md`

- [ ] **Step 1: добавить один acceptance test завершённого, failed и cancelled run**

В существующих worker/admin tests построить три synthetic runs без внешних
providers. Для каждого доказать:

1. public GET/SSE содержит только registry projection;
2. admin search находит по exact user ID и run ID;
3. timeline/export после нового storage/recorder instance содержит ordered
   events, model attempt metadata, redacted diagnostics и screenshot checksum;
4. export не меняет token/cost counters и не вызывает provider;
5. cleanup на `finished_at + 120h - 1µs` оставляет private evidence, а ровно на
   границе удаляет private files/manifest, сохраняя core rows/artifacts/model
   accounting;
6. другой owner получает 404 до и после cleanup.

- [ ] **Step 2: выполнить полную проверку с disposable PostgreSQL**

```powershell
python -m pytest tests/builder_lab_cases/test_generation_event_registry.py tests/builder_lab_cases/test_generation_forensics_redaction.py tests/builder_lab_cases/test_generation_forensics_storage.py tests/builder_lab_cases/test_generation_forensics_cleanup.py -q
python -m pytest tests/saas_cases/test_generation_forensics_migration.py tests/saas_cases/test_generation_forensics_admin.py tests/saas_cases/test_model_router.py tests/saas_cases/test_project_routes.py -q
python -m pytest tests/saas_cases tests/builder_lab_cases tests/deployment_cases -q
docker compose config --quiet
python scripts/preflight_saas_schema.py --database-url $env:KAIGO_TEST_POSTGRES_URL
git diff --check
git status --short --branch
```

Expected: все suites PASS, один Alembic head
`0014_generation_forensics`, compose valid, diff clean. PostgreSQL proof обязателен;
SQLite-only PASS недостаточен для завершения schema/cleanup work.

- [ ] **Step 3: выполнить security-focused ручную инспекцию**

```powershell
rg -n "GenerationEvent\(" app builder_lab
rg -n "diagnostic|Authorization|Cookie|access_token|refresh_token|api_key|password" builder_lab/forensics app/admin/generation_forensics.py
rg -n "generation-forensics|builder-evidence|reference-evidence" docker-compose.yml deploy/systemd docs/SAAS_PRODUCTION_RUNBOOK.md
git diff --stat
git diff -- . ':(exclude)docs/product-journal/2026-07.md' ':(exclude)docs/telegram/release-packets/2026-07-30-generation-forensics.md' ':(exclude)docs/telegram/content-backlog.md'
```

Review every hit, not just counts. Confirm:

- all new generation writers use enum + shared preparation;
- no raw private payload is interpolated in log/exception;
- no arbitrary path comes from HTTP request;
- cleanup cannot touch active/foreign/unmarked dirs;
- five-day cleanup leaves core event/model/artifact rows untouched;
- feature flag remains false in `.env.example` and no production secrets are
  committed;
- reference cleanup and funnel events are unchanged.

- [ ] **Step 4: обновить подтверждённую документацию после GREEN**

В backlog сменить status только на фактически достигнутый локальный результат и
сослаться на tests/commands. В journal и release packet по-русски отделить:

- подтверждённые registry/redaction/storage/admin/cleanup результаты;
- то, что persistent Docker smoke прошёл локально;
- отдельный remaining operator gate: установка units, включение feature и
  проверка timer/volume на production host.

Добавить тему в content backlog, но ничего не публиковать в Telegram. Asset
registry менять только если реально создан безопасный screenshot без private
данных; не генерировать декоративное изображение ради чекбокса.

- [ ] **Step 5: запросить независимый code review и устранить findings**

Использовать skill `requesting-code-review`. Review scope: commits Tasks 1–10,
approved spec и этот plan. Особо попросить проверить path traversal/symlink/TOCTOU,
public projection leakage, fail-open recorder boundary, TTL off-by-one, quota
ordering, async blocking I/O и migration downgrade. После исправлений повторить
Step 2 целиком.

- [ ] **Step 6: final commit и handoff**

```powershell
git add docs/GENERATION_FORENSICS_BACKLOG.md docs/product-journal/2026-07.md docs/telegram/release-packets/2026-07-30-generation-forensics.md docs/telegram/content-backlog.md
git commit -m "docs: record generation forensics verification"
git status --short --branch
git log --oneline --decorate -12
```

Expected: clean task worktree. Handoff должен перечислить commits, точные
команды/результаты, disposable PostgreSQL revision/fingerprint proof, локальный
Docker persistence proof и не выполненный production operator gate. Не
утверждать, что timer активен на production, пока это не проверено отдельно.

## Self-review checklist для исполнителя

- [ ] Реестр содержит все и только 36 текущих generation types; funnel taxonomy
  не смешана с ним.
- [ ] Unknown legacy event не ломает GET/SSE и не раскрывает raw type/message.
- [ ] Stored `public_payload` не является доверенным bypass для registry.
- [ ] New writes проходят recursive secret/PII redaction до DB/file boundary.
- [ ] Operational `sequence` остаётся единственным порядком событий.
- [ ] Prompt/response/raw/tool/diagnostic/screenshot bytes отсутствуют в
  `model_calls` и публичном API.
- [ ] Manifest path relative, checksum проверяется до admin read/export.
- [ ] TTL начинается от первого terminal `finished_at`, равен 120 часам и не
  сдвигается повторным hook.
- [ ] Quota удаляет только валидные marked terminal run dirs; active и foreign
  paths остаются.
- [ ] Cleanup удаляет private manifest/files, но не core run/event/artifact/model
  rows.
- [ ] Forensic admin allowlist отдельный, непустой и fail-closed; allow/deny
  действия audit-ятся.
- [ ] Export streaming, redacted, checksum-verified и не вызывает модель.
- [ ] Feature выключена по умолчанию, existing reference tmpfs/timer сохранены.
- [ ] Migration ровно `0014_generation_forensics`, additive и проверена на
  disposable PostgreSQL в upgrade/downgrade/upgrade цикле.
- [ ] Никакие чужие dirty-файлы не попали в commits; production rollout не
  выполнен без отдельного разрешения.
