# Kaigo Agent Kernel Pattern Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить в существующий durable Builder Lab версионируемую библиотеку проверенных UI-паттернов, отдельную стадию выбора композиции, PostgreSQL provenance/outcomes и воспроизводимый benchmark GLM-5.2/GPT-5.5.

**Architecture:** Текущие worker, visual committee, router и публикация сохраняются. После `art_direction` появляется durable stage `composition`, которая возвращает только типизированные IDs и параметры; deterministic resolver загружает точные Git-assets выбранных версий, а последующие стадии получают bounded bundle. PostgreSQL хранит immutable plan и результаты, но implementation assets остаются в Git.

**Tech Stack:** Python 3.12, dataclasses, JSON Schema subset, aiohttp, SQLAlchemy 2, Alembic, PostgreSQL 15, pytest, existing model router, Codex in-app Browser.

---

## Карта файлов

- `builder_lab/patterns/models.py` — чистые immutable domain contracts.
- `builder_lab/patterns/registry.py` — загрузка, validation и hashing Git-каталога.
- `builder_lab/patterns/resolver.py` — совместимость и bounded implementation bundle.
- `builder_lab/patterns/planner.py` — structured planner contract и prompt.
- `builder_lab/patterns/catalog/*` — manifest и implementation assets первой библиотеки.
- `builder_lab/modes.py` — durable `composition` stage и routing role.
- `builder_lab/worker.py` — исполнение planner stage и восстановление plan.
- `builder_lab/engines/base.py` — provider-neutral planner protocol.
- `builder_lab/engines/gemini_direct.py` — structured planner call через существующий transport.
- `builder_lab/prompts.py` — bounded planner и generator bundle prompts.
- `app/saas/models.py` — ORM provenance и outcomes.
- `migrations/versions/0013_pattern_registry.py` — PostgreSQL schema.
- `scripts/compare_builder_models.py` — frozen-input benchmark.
- `tests/builder_lab_cases/test_pattern_*.py` — unit/integration contracts.
- `tests/saas_cases/test_pattern_registry_schema.py` — migration/ORM contracts.

### Task 1: Pure domain contracts

**Files:**
- Create: `builder_lab/patterns/__init__.py`
- Create: `builder_lab/patterns/models.py`
- Test: `tests/builder_lab_cases/test_pattern_models.py`

- [ ] **Step 1: Write the failing contract tests**

```python
def test_composition_plan_requires_each_core_slot_once():
    with pytest.raises(ValueError, match="required slots"):
        CompositionPlan(
            schema_version=1,
            direction_id="candidate-2",
            selections=(selection("launcher", "orb-pulse"),),
            summary="Неполный план",
        )

def test_selection_rejects_code_in_parameters():
    with pytest.raises(ValueError, match="parameters"):
        selection("launcher", "orb-pulse", {"javascript": "alert(1)"})

def test_plan_round_trip_is_canonical():
    original = complete_plan()
    assert CompositionPlan.from_dict(original.to_dict()) == original
```

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/builder_lab_cases/test_pattern_models.py -q`  
Expected: FAIL because `builder_lab.patterns.models` does not exist.

- [ ] **Step 3: Implement the minimal immutable contracts**

```python
class PatternCategory(str, Enum):
    LAUNCHER = "launcher"
    SHELL = "shell"
    MESSAGES = "messages"
    COMPOSER = "composer"
    MOTION = "motion"

@dataclass(frozen=True, slots=True)
class PatternSelection:
    slot: PatternCategory
    pattern_id: str
    version: int
    parameters: Mapping[str, JSONValue]
    reason: str

@dataclass(frozen=True, slots=True)
class CompositionPlan:
    schema_version: int
    direction_id: str
    selections: tuple[PatternSelection, ...]
    summary: str
    custom_escape: CustomPatternEscape | None = None
```

Validation must bound strings, JSON size, parameters, duplicate slots and require
all five categories unless that exact category is covered by `custom_escape`.

- [ ] **Step 4: Run GREEN and commit**

Run: `python -m pytest tests/builder_lab_cases/test_pattern_models.py -q`  
Expected: PASS.  
Commit: `feat: add pattern composition contracts`

### Task 2: Git-backed registry and first verified catalog

**Files:**
- Create: `builder_lab/patterns/registry.py`
- Create: `builder_lab/patterns/catalog/<pattern-id>/manifest.json`
- Create: `builder_lab/patterns/catalog/<pattern-id>/fragment.html`
- Create: `builder_lab/patterns/catalog/<pattern-id>/styles.css`
- Create: `builder_lab/patterns/catalog/<pattern-id>/behavior.js`
- Test: `tests/builder_lab_cases/test_pattern_registry.py`

- [ ] **Step 1: Write registry failure tests**

```python
def test_registry_rejects_asset_hash_mismatch(tmp_path):
    write_pattern(tmp_path, implementation_sha256="0" * 64)
    with pytest.raises(PatternRegistryError, match="hash"):
        PatternRegistry.load(tmp_path)

def test_registry_rejects_duplicate_active_versions(tmp_path):
    write_pattern(tmp_path / "a", pattern_id="orb-pulse", version=1)
    write_pattern(tmp_path / "b", pattern_id="orb-pulse", version=1)
    with pytest.raises(PatternRegistryError, match="duplicate"):
        PatternRegistry.load(tmp_path)

def test_builtin_catalog_has_two_patterns_per_category():
    registry = load_builtin_registry()
    for category in PatternCategory:
        assert len(registry.active_for(category)) >= 2
```

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/builder_lab_cases/test_pattern_registry.py -q`  
Expected: FAIL because registry and catalog are absent.

- [ ] **Step 3: Implement strict loader**

```python
@dataclass(frozen=True, slots=True)
class PatternDefinition:
    pattern_id: str
    version: int
    category: PatternCategory
    status: PatternStatus
    description: str
    parameter_schema: Mapping[str, JSONValue]
    incompatible_with: tuple[str, ...]
    implementation_sha256: str
    html: str
    css: str
    javascript: str

class PatternRegistry:
    @classmethod
    def load(cls, root: Path) -> "PatternRegistry": ...
    def resolve(self, pattern_id: str, version: int) -> PatternDefinition: ...
    def public_catalog(self) -> tuple[dict[str, JSONValue], ...]: ...
```

Read only expected filenames, reject symlinks/path escapes, cap each asset and
the full catalog, parse UTF-8 strictly and recompute SHA-256 over canonical
manifest fields plus asset bytes.

- [ ] **Step 4: Add ten initial patterns**

Add exactly these active version-1 IDs: `orb-pulse`, `peek-tab`, `compact-chat`,
`floating-card`, `paired-bubbles`, `advisor-cards`, `single-line-pill`,
`multiline-soft`, `spring-reveal`, `soft-scale`. Assets must expose CSS custom
properties for permitted parameters and must not contain remote URLs, dynamic
code loading, storage access or network calls.

- [ ] **Step 5: Run GREEN and commit**

Run: `python -m pytest tests/builder_lab_cases/test_pattern_registry.py -q`  
Expected: PASS with ten loaded active patterns.  
Commit: `feat: add verified widget pattern catalog`

### Task 3: Deterministic resolver

**Files:**
- Create: `builder_lab/patterns/resolver.py`
- Test: `tests/builder_lab_cases/test_pattern_resolver.py`

- [ ] **Step 1: Write failing compatibility and bound tests**

```python
def test_resolver_rejects_incompatible_pair(registry):
    plan = plan_with("peek-tab", "floating-card")
    with pytest.raises(PatternResolutionError, match="incompatible"):
        resolve_composition(plan, registry)

def test_resolver_returns_only_selected_assets(registry):
    bundle = resolve_composition(complete_plan(), registry)
    assert set(bundle.pattern_ids) == {item.pattern_id for item in complete_plan().selections}
    assert "unused-pattern-marker" not in bundle.prompt_text

def test_resolved_bundle_is_bounded(registry):
    assert len(resolve_composition(complete_plan(), registry).prompt_text.encode()) <= 96_000
```

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/builder_lab_cases/test_pattern_resolver.py -q`  
Expected: FAIL because resolver is absent.

- [ ] **Step 3: Implement resolution**

```python
@dataclass(frozen=True, slots=True)
class ResolvedComposition:
    plan: CompositionPlan
    pattern_ids: tuple[str, ...]
    implementation_hashes: tuple[str, ...]
    prompt_text: str

def resolve_composition(
    plan: CompositionPlan,
    registry: PatternRegistry,
    *,
    max_prompt_bytes: int = 96_000,
) -> ResolvedComposition: ...
```

Validate parameter schemas before interpolation. Keep assets delimited as
untrusted reference code; resolver never executes JavaScript.

- [ ] **Step 4: Run GREEN and commit**

Run: `python -m pytest tests/builder_lab_cases/test_pattern_resolver.py -q`  
Expected: PASS.  
Commit: `feat: resolve bounded pattern compositions`

### Task 4: Structured planner and durable stage

**Files:**
- Create: `builder_lab/patterns/planner.py`
- Modify: `builder_lab/models.py`
- Modify: `builder_lab/modes.py`
- Modify: `builder_lab/engines/base.py`
- Modify: `builder_lab/engines/gemini_direct.py`
- Modify: `builder_lab/prompts.py`
- Modify: `builder_lab/worker.py`
- Test: `tests/builder_lab_cases/test_pattern_planner.py`
- Test: `tests/builder_lab_cases/test_worker.py`

- [ ] **Step 1: Write planner validation tests**

```python
async def test_planner_repairs_invalid_model_contract_once(fake_engine, registry):
    fake_engine.plan_results = [{"selections": []}, complete_plan().to_dict()]
    result = await plan_composition(fake_engine, request(), direction(), registry)
    assert result.plan == complete_plan()
    assert fake_engine.plan_attempts == 2

async def test_worker_resumes_after_composition_without_replanning(queue, engine):
    await run_until_stage_completed(queue, engine, "composition")
    engine.reset_calls()
    await restart_worker(queue, engine)
    assert engine.plan_composition_calls == 0
    assert engine.generate_calls[0].stage is Stage.FOUNDATION
```

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/builder_lab_cases/test_pattern_planner.py tests/builder_lab_cases/test_worker.py -q`  
Expected: FAIL because `Stage.COMPOSITION` and planner protocol are absent.

- [ ] **Step 3: Add provider-neutral planner protocol**

```python
@dataclass(frozen=True, slots=True)
class CompositionPlanResult:
    plan: CompositionPlan
    usage: TokenUsage = TokenUsage()
    provider_request_id: str | None = None

class DirectBuilderEngine(BuilderEngine, Protocol):
    async def plan_composition(
        self,
        *,
        request: BuilderRequest,
        selected_direction: DirectionProposal,
        catalog: tuple[dict[str, JSONValue], ...],
        correction: str | None = None,
    ) -> CompositionPlanResult: ...
```

Add `Stage.COMPOSITION`, insert `composition` between `art_direction` and
`foundation` in direct/express policies and route it as `composition_planner`.

- [ ] **Step 4: Implement structured prompt and one corrective retry**

The planner system prompt explicitly forbids HTML/CSS/JS, requires one selection
per category, accepts only IDs from the untrusted catalog and limits `reason` to
200 characters. Parse with `CompositionPlan.from_dict`, then resolve locally.

- [ ] **Step 5: Persist the stage before generation continues**

The composition stage returns no artifact. It stores `composition_plan` and
resolved hashes in `StageResult.context`; the next `foundation` attempt restores
them and passes `ResolvedComposition.prompt_text` to `execute_stage`. A restart
after `stage.completed` must not call planner again.

- [ ] **Step 6: Run GREEN and commit**

Run: `python -m pytest tests/builder_lab_cases/test_pattern_planner.py tests/builder_lab_cases/test_worker.py -q`  
Expected: PASS including restart test.  
Commit: `feat: add durable composition planning stage`

### Task 5: PostgreSQL provenance and outcomes

**Files:**
- Modify: `app/saas/models.py`
- Create: `migrations/versions/0013_pattern_registry.py`
- Create: `app/patterns/repository.py`
- Test: `tests/saas_cases/test_pattern_registry_schema.py`
- Test: `tests/saas_cases/test_pattern_repository.py`

- [ ] **Step 1: Write failing schema constraints**

```python
EXPECTED = {
    "widget_pattern_versions",
    "composition_plans",
    "composition_plan_items",
    "pattern_outcomes",
}

def test_pattern_tables_are_registered():
    assert EXPECTED <= set(Base.metadata.tables)

async def test_one_slot_cannot_be_persisted_twice(repository, plan_id):
    await repository.add_item(plan_id, "launcher", "orb-pulse", 1, {})
    with pytest.raises(IntegrityError):
        await repository.add_item(plan_id, "launcher", "peek-tab", 1, {})
```

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/saas_cases/test_pattern_registry_schema.py tests/saas_cases/test_pattern_repository.py -q`  
Expected: FAIL listing missing tables.

- [ ] **Step 3: Add ORM and migration**

Required constraints:

```python
UniqueConstraint("pattern_id", "version", name="uq_pattern_version")
UniqueConstraint("composition_plan_id", "slot", name="uq_composition_slot")
CheckConstraint("version > 0", name="ck_pattern_version_positive")
CheckConstraint("repair_count >= 0", name="ck_pattern_outcome_repairs_nonnegative")
```

Use UUID keys, JSONB parameters/manifests on PostgreSQL, integer micro-USD and
immutable timestamps. Foreign keys to run/artifact/model_call use explicit names
and deterministic delete behavior.

- [ ] **Step 4: Verify PostgreSQL 15 upgrade/downgrade/upgrade**

Run against disposable PostgreSQL 15:

```powershell
alembic upgrade head
alembic downgrade 0012_worker_service_readiness
alembic upgrade head
python -m pytest tests/saas_cases/test_pattern_registry_schema.py tests/saas_cases/test_pattern_repository.py -q
```

Expected: every command succeeds and reflected constraints match canonical names.

- [ ] **Step 5: Commit**

Commit: `feat: persist pattern composition provenance`

### Task 6: Outcome telemetry and model-call linkage

**Files:**
- Modify: `builder_lab/worker.py`
- Modify: `builder_lab/visual_gate.py`
- Modify: `app/patterns/repository.py`
- Test: `tests/builder_lab_cases/test_pattern_outcomes.py`

- [ ] **Step 1: Write failing outcome test**

```python
async def test_final_gate_records_one_outcome_per_selected_pattern(run):
    await complete_run(run, visual_score=0.86, repair_count=2)
    outcomes = await load_outcomes(run.id)
    assert len(outcomes) == 5
    assert {row.visual_score for row in outcomes} == {0.86}
    assert {row.repair_count for row in outcomes} == {2}
    assert sum(row.cost_microusd for row in outcomes) == run.pattern_attributed_cost
```

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/builder_lab_cases/test_pattern_outcomes.py -q`  
Expected: FAIL because no outcomes are written.

- [ ] **Step 3: Record idempotent terminal outcomes**

Write outcomes only when a run reaches final accepted/fallback state. Use an
idempotency key derived from `run_id + plan_item_id + final_artifact_id` so worker
restart cannot double-count. Attribute cost deterministically by stage/model-call
links; do not estimate missing provider usage as real spend.

- [ ] **Step 4: Run GREEN and commit**

Run: `python -m pytest tests/builder_lab_cases/test_pattern_outcomes.py -q`  
Expected: PASS including duplicate-restart case.  
Commit: `feat: record pattern quality outcomes`

### Task 7: Frozen-input GLM-5.2 versus GPT-5.5 benchmark

**Files:**
- Create: `scripts/compare_builder_models.py`
- Create: `tests/model_cases/test_compare_builder_models.py`
- Create: `docs/model-benchmarks/README.md`
- Modify: `.gitignore`

- [ ] **Step 1: Write failing offline report test**

```python
def test_report_compares_same_evidence_and_plan(tmp_path):
    report = build_report(load_fixture_runs())
    assert report["input_identity"]["evidence_sha256"] == "evidence-1"
    assert report["input_identity"]["composition_sha256"] == "plan-1"
    assert {row["model"] for row in report["runs"]} == {"glm-5.2", "gpt-5.5"}
    assert all("cost_rub" in row and "publishable" in row for row in report["runs"])
```

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/model_cases/test_compare_builder_models.py -q`  
Expected: FAIL because benchmark script is absent.

- [ ] **Step 3: Implement benchmark runner and redaction**

CLI accepts `--evidence`, `--composition`, repeated `--target provider:model`,
`--usd-to-rub` and `--output`. It writes JSON plus a Russian Markdown summary
with tokens, cost, latency, retries, repairs, visual score and publishability.
API keys, prompts containing private page text and authorization headers are not
written. Raw outputs go to ignored `data/benchmarks/private/`.

- [ ] **Step 4: Run offline GREEN, then real equal-input runs**

Run: `python -m pytest tests/model_cases/test_compare_builder_models.py -q`  
Expected: PASS. Then run one real GLM-5.2 and one GPT-5.5 generation only after
PostgreSQL and provider configs pass preflight. Preserve provider usage receipts.

- [ ] **Step 5: Commit**

Commit: `feat: add reproducible builder model benchmark`

### Task 8: Unified verification, Browser acceptance and release

**Files:**
- Modify: `docs/SAAS_PRODUCTION_RUNBOOK.md`
- Modify: `docs/KAIGO_SPACE_OPERATIONS.md`
- Modify: `docs/product-journal/2026-07.md`
- Create: `docs/telegram/release-packets/2026-07-29-agent-kernel.md`

- [ ] **Step 1: Run unified local checks**

```powershell
python -m pytest tests/builder_lab_cases tests/saas_cases tests/model_cases -q
python -m ruff check app builder_lab scripts tests
python -m compileall app builder_lab scripts
npm --prefix frontend run test -- --run
npm --prefix frontend run typecheck
npm --prefix frontend run lint
npm --prefix frontend run build
docker compose config --quiet
git diff --check
```

Expected: all exit zero. Record exact counts, skips and durations.

- [ ] **Step 2: Verify disposable PostgreSQL 15**

Start a named temporary PostgreSQL 15 container, migrate to head, run schema,
worker, auth, project, publication and pattern tests, then remove only that named
container. Expected: no container or process remains after verification.

- [ ] **Step 3: Stage a production canary**

Use `/root/ai_project`, a unique release directory and immutable image IDs. Run
preflight, schema check, `/api/health`, tokenized `/api/ready` and edge canary
before changing the public upstream. Never touch `/root/kaigo`.

- [ ] **Step 4: Perform acceptance through Codex in-app Browser**

Using the `browser:control-in-app-browser` skill, verify desktop and mobile:

1. landing composer preserves URL/brief;
2. configured auth gate is reachable without completing real provider login;
3. test-auth or fixture path creates/restores a project;
4. refresh restores run/events/preview;
5. completed artifact opens, closes and sends a two-turn chat;
6. publish/upgrade gates tell the truth for the current subscription state;
7. published embed loads on the canary host.

Capture screenshots and console errors. Standalone terminal Playwright is not a
substitute for this acceptance step.

- [ ] **Step 5: Commit exact SaaS scope and push**

Inspect `git diff --name-only` and stage explicit SaaS/Agent Kernel paths only.
Do not stage `tools/codex_telegram_bridge`, Telegram drafts, published log or
unrelated assets. Push `codex/saas-foundation`; do not rewrite history.

- [ ] **Step 6: Promote or roll back**

Promote only when PostgreSQL, readiness and Browser checks pass. Otherwise keep
the existing public release and report the exact failing gate. Verify rollback
uses the previously snapshotted app/worker images and Alembic revision.

## Self-review

- Spec coverage: registry, planner, deterministic resolution, PostgreSQL,
  outcomes, benchmark, Browser and release each have an owning task.
- Placeholder scan: every implementation step and expected verification result is explicit.
- Type consistency: `CompositionPlan`, `PatternSelection`,
  `ResolvedComposition` and `CompositionPlanResult` keep the same names across
  domain, provider, worker and persistence tasks.
