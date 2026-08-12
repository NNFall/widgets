# Kaigo Stage-Aware Pattern Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить в Kaigo версиярованную библиотеку атомарных UI/motion-паттернов schema v3, AI-shortlist из 2–5 кандидатов, stage-aware передачу кода, PostgreSQL provenance и техническую admin-only Pattern Lab без pgvector и без замены legacy planner по умолчанию.

**Architecture:** Старый каталог manifest v1/v2 и exact-one `CompositionPlan` остаются неизменными. Новый контур живёт в отдельных `atomic_*`/`candidate_*` модулях, сохраняет shortlist до первой генерации, выдаёт каждой стадии только релевантные exact versions и включается отдельным feature flag. Git остаётся source of truth для manifest/assets; PostgreSQL хранит review state, планы, exposures и usage claims.

**Tech Stack:** Python 3.12, dataclasses/Enum, JSON Schema, SQLAlchemy 2 async, Alembic, PostgreSQL, aiohttp admin routes, pytest/pytest-asyncio.

---

## File map

Новые файлы:

- `builder_lab/patterns/atomic_models.py` — 14 категорий, lifecycle/policy, schema v2 shortlist и stage mapping.
- `builder_lab/patterns/atomic_registry.py` — безопасная загрузка manifest schema v3 и immutable assets/hash.
- `builder_lab/patterns/candidate_planner.py` — AI-вызов, server validation, один correction retry и deterministic fallback.
- `builder_lab/patterns/candidate_resolver.py` — построение bounded reference pack для конкретной generation stage.
- `builder_lab/patterns/atomic_catalog/*` — по одной нейтральной технической fixture на каждую категорию.
- `app/patterns/candidate_repository.py` — синхронизация v3 registry, review state, durable shortlist, exposures и claims.
- `app/admin/pattern_lab.py` — admin list/detail/preview/review endpoints.
- `migrations/versions/0018_stage_aware_pattern_library.py` — только additive PostgreSQL schema.
- `tests/builder_lab_cases/test_atomic_pattern_registry.py` — manifest v3 и hash.
- `tests/builder_lab_cases/test_pattern_candidate_planner.py` — prompt, shortlist, retry/fallback.
- `tests/builder_lab_cases/test_pattern_candidate_resolver.py` — stage mapping и exact code packs.
- `tests/saas_cases/test_pattern_candidate_repository.py` — persistence/restart/exposure/claims/reviews.
- `tests/saas_cases/test_pattern_candidate_migration.py` — Alembic upgrade/downgrade contract.
- `tests/saas_cases/test_pattern_lab.py` — admin auth, sandbox и review actions.
- `tests/builder_lab_cases/test_pattern_candidate_worker.py` — feature flag и durable worker hand-off.

Изменяемые файлы:

- `builder_lab/prompts.py` — JSON schema selector, selector prompt и optional stage candidate bundle.
- `builder_lab/engines/base.py` — тип результата и protocol method `plan_pattern_candidates`.
- `builder_lab/engines/gemini_direct.py` — один bounded structured call для selector.
- `builder_lab/config.py` — `KAIGO_PATTERN_CANDIDATE_PLAN_V2_ENABLED`, default `false`.
- `builder_lab/worker.py` — новый путь на composition и stage-aware resolver под feature flag.
- `builder_lab/patterns/__init__.py` — публичные экспорты нового контура без удаления legacy API.
- `app/saas/models.py` — SQLAlchemy records шести новых таблиц.
- `app/admin/routes.py` — регистрация Pattern Lab routes.
- `.env.example` — документирование выключенного feature flag.
- `docs/superpowers/specs/2026-08-05-kaigo-stage-aware-pattern-library-design.md` — статус «утверждено пользователем».

### Task 1: Atomic domain model, schema v3 registry and neutral fixtures

**Files:**

- Create: `builder_lab/patterns/atomic_models.py`
- Create: `builder_lab/patterns/atomic_registry.py`
- Create: `builder_lab/patterns/atomic_catalog/<category>-technical-v1/{manifest.json,fragment.html,styles.css,behavior.js}` for all 14 categories
- Create: `tests/builder_lab_cases/test_atomic_pattern_registry.py`
- Modify: `builder_lab/patterns/__init__.py`
- Modify: `docs/superpowers/specs/2026-08-05-kaigo-stage-aware-pattern-library-design.md`

- [ ] **Step 1: Write failing domain and loader tests**

```python
def test_v3_registry_exposes_full_ai_description_without_assets():
    registry = load_builtin_atomic_registry()
    definition = registry.resolve("widget-open-technical", 1)
    public = definition.selector_dict()
    assert public["ai_description"] == definition.ai_description
    assert "html" not in public and "css" not in public and "javascript" not in public

def test_v3_registry_rejects_hash_drift(tmp_path):
    root = valid_atomic_catalog(tmp_path)
    (root / "widget-open-technical-v1" / "styles.css").write_text("changed", encoding="utf-8")
    with pytest.raises(AtomicPatternRegistryError, match="implementation hash"):
        AtomicPatternRegistry.load(root)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python -m pytest -q tests/builder_lab_cases/test_atomic_pattern_registry.py`

Expected: FAIL because `atomic_models` and `atomic_registry` do not exist.

- [ ] **Step 3: Implement the immutable domain contract**

```python
class AtomicPatternCategory(str, Enum):
    LAUNCHER_SHAPE = "launcher_shape"
    LAUNCHER_IDLE = "launcher_idle"
    LAUNCHER_ATTENTION = "launcher_attention"
    SHELL_LAYOUT = "shell_layout"
    WIDGET_OPEN = "widget_open"
    WIDGET_CLOSE = "widget_close"
    BACKGROUND_EFFECT = "background_effect"
    ASSISTANT_MESSAGE_ENTER = "assistant_message_enter"
    USER_MESSAGE_ENTER = "user_message_enter"
    TYPING_INDICATOR = "typing_indicator"
    MESSAGE_SEND = "message_send"
    COMPOSER_FOCUS = "composer_focus"
    CONTROL_HOVER = "control_hover"
    RESPONSIVE_TRANSITION = "responsive_transition"

class AtomicPatternStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"

class AdaptationPolicy(str, Enum):
    STRICT = "strict"
    ADAPTIVE = "adaptive"
    CREATIVE = "creative"
```

`AtomicPatternDefinition.selector_dict()` MUST include title, summary and the complete `ai_description`, but MUST omit implementation assets. `implementation_dict()` returns exact immutable html/css/javascript only after shortlist validation.

- [ ] **Step 4: Implement the schema v3 loader and deterministic hash**

```python
@classmethod
def load(cls, root: Path) -> "AtomicPatternRegistry":
    resolved_root = root.resolve(strict=True)
    definitions = tuple(
        _load_atomic_definition(directory, catalog_root=resolved_root)
        for directory in sorted(resolved_root.iterdir(), key=lambda item: item.name)
    )
    return cls(definitions=definitions)

def selector_catalog(self) -> tuple[dict[str, JSONValue], ...]:
    return tuple(
        item.selector_dict()
        for item in self.definitions
        if item.status is AtomicPatternStatus.ACTIVE
    )
```

`ai_description` is mandatory, 80–4000 characters, natural language, NUL-free. `technical_contract`, `adaptation_policy`, `incompatible_with`, provenance and SHA-256 are mandatory. `behavior.js` is optional and defaults to an empty string; preview executes it only inside a sandbox.

- [ ] **Step 5: Add one neutral technical fixture for every category**

Every fixture uses its own root class, has no external URL/storage/network access, describes the intended adaptation freedom in `ai_description`, and starts with `provenance.review_state="approved"` only so contract tests can exercise every category. These fixtures are technical references, not the final visual catalog.

- [ ] **Step 6: Run focused tests and legacy registry tests**

Run: `python -m pytest -q tests/builder_lab_cases/test_atomic_pattern_registry.py tests/builder_lab_cases/test_pattern_registry.py tests/builder_lab_cases/test_pattern_catalog_v2.py`

Expected: PASS; v1/v2 catalog tests remain unchanged.

- [ ] **Step 7: Mark the approved design and commit**

```text
git add builder_lab/patterns/atomic_* builder_lab/patterns/__init__.py tests/builder_lab_cases/test_atomic_pattern_registry.py docs/superpowers/specs/2026-08-05-kaigo-stage-aware-pattern-library-design.md
git commit -m "feat: add atomic pattern registry v3"
```

### Task 2: AI shortlist contract, full-description prompt and fallback

**Files:**

- Create: `builder_lab/patterns/candidate_planner.py`
- Create: `tests/builder_lab_cases/test_pattern_candidate_planner.py`
- Modify: `builder_lab/patterns/atomic_models.py`
- Modify: `builder_lab/prompts.py`
- Modify: `builder_lab/engines/base.py`
- Modify: `builder_lab/engines/gemini_direct.py`

- [ ] **Step 1: Write failing selector tests**

```python
def test_selector_prompt_contains_full_ai_description_but_no_assets():
    registry = approved_registry()
    prompt = build_pattern_candidate_plan_prompt(
        request=builder_request(),
        selected_direction=direction_proposal(),
        selector_catalog=registry.selector_catalog(),
        correction=None,
    )
    assert FULL_AI_DESCRIPTION in prompt
    assert "BEGIN fragment.html" not in prompt
    assert "<div class=" not in prompt

@pytest.mark.asyncio
async def test_repeated_invalid_selector_output_uses_deterministic_fallback():
    engine = FakeSelectorEngine([invalid_payload, invalid_payload])
    result = await plan_pattern_candidates(engine, request, direction, registry)
    assert result.used_fallback is True
    assert all(1 <= len(group.candidates) <= 5 for group in result.plan.groups)
```

- [ ] **Step 2: Run selector tests and verify RED**

Run: `python -m pytest -q tests/builder_lab_cases/test_pattern_candidate_planner.py`

Expected: FAIL because the candidate planner API does not exist.

- [ ] **Step 3: Implement schema v2 shortlist models**

```python
@dataclass(frozen=True, slots=True)
class PatternCandidate:
    pattern_id: str
    version: int
    rank: int
    reason: str

@dataclass(frozen=True, slots=True)
class PatternCandidateGroup:
    category: AtomicPatternCategory
    candidates: tuple[PatternCandidate, ...]

@dataclass(frozen=True, slots=True)
class PatternCandidatePlan:
    schema_version: int
    direction_id: str
    groups: tuple[PatternCandidateGroup, ...]
    summary: str
```

Validation requires unique categories, unique exact versions within each group, canonical rank `1..N`, 2–5 candidates when at least two eligible definitions exist, one candidate when only one exists, and an empty group only for an optional empty category.

- [ ] **Step 4: Add a bounded structured schema and prompt**

```python
PATTERN_CANDIDATE_PLAN_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "direction_id", "groups", "summary"],
    "properties": {
        "schema_version": {"type": "integer", "enum": [2]},
        "direction_id": {"type": "string", "minLength": 1, "maxLength": 80},
        "groups": {"type": "array", "minItems": 1, "maxItems": 14, "items": CANDIDATE_GROUP_SCHEMA},
        "summary": {"type": "string", "minLength": 1, "maxLength": 1000},
    },
}
```

The prompt explicitly says: inspect `title`, `summary` and complete `ai_description`; return IDs/reasons only; never return or invent code; select 2–5 unless the eligible catalog contains one; do not omit categories silently.

- [ ] **Step 5: Add engine protocol and GeminiDirectEngine structured call**

```python
async def plan_pattern_candidates(
    self,
    *,
    request: BuilderRequest,
    selected_direction: DirectionProposal,
    selector_catalog: tuple[dict[str, Any], ...],
    correction: str | None = None,
) -> PatternCandidatePlanResult:
    raise NotImplementedError
```

Use operation `pattern_candidate_plan`, temperature `0.2`, the same provider router/deadline mechanism as `plan_composition`, and preserve usage/request IDs.

- [ ] **Step 6: Implement server validation, one retry and deterministic fallback**

The validator resolves every exact version from registry, checks active status, effective approved set passed by caller, category, duplicates and symmetric incompatibilities. On first invalid response, pass a bounded validation diagnostic back once. On second invalid response, choose the first up-to-five eligible definitions sorted by `(category, pattern_id, version)` and mark `used_fallback=True`; selector failure must not fail the generation.

- [ ] **Step 7: Run focused and direct-engine tests, then commit**

Run: `python -m pytest -q tests/builder_lab_cases/test_pattern_candidate_planner.py tests/builder_lab_cases/test_gemini_direct.py`

Expected: PASS.

```text
git add builder_lab/patterns/candidate_planner.py builder_lab/patterns/atomic_models.py builder_lab/prompts.py builder_lab/engines/base.py builder_lab/engines/gemini_direct.py tests/builder_lab_cases/test_pattern_candidate_planner.py
git commit -m "feat: select atomic pattern candidates"
```

### Task 3: Stage-aware exact-version reference packs

**Files:**

- Create: `builder_lab/patterns/candidate_resolver.py`
- Create: `tests/builder_lab_cases/test_pattern_candidate_resolver.py`
- Modify: `builder_lab/patterns/atomic_models.py`
- Modify: `builder_lab/prompts.py`
- Modify: `builder_lab/engines/base.py`
- Modify: `builder_lab/engines/gemini_direct.py`

- [ ] **Step 1: Write failing stage mapping and exact-code tests**

```python
@pytest.mark.parametrize(("stage", "expected"), [
    (Stage.FOUNDATION, {"launcher_shape", "shell_layout", "background_effect"}),
    (Stage.IDENTITY, {"launcher_attention", "launcher_idle"}),
    (Stage.CONVERSATION, {"assistant_message_enter", "user_message_enter", "typing_indicator", "message_send", "composer_focus"}),
    (Stage.MOTION_POLISH, {"widget_open", "widget_close", "control_hover", "responsive_transition", "background_effect"}),
])
def test_stage_pack_contains_only_mapped_categories(stage, expected):
    pack = resolve_pattern_candidate_pack(candidate_plan(), stage, atomic_registry())
    assert {item.category.value for item in pack.exposed_versions} == expected

def test_stage_pack_uses_only_exact_shortlisted_versions():
    plan = candidate_plan(widget_open=("wave-reveal", 1))
    pack = resolve_pattern_candidate_pack(plan, Stage.MOTION_POLISH, atomic_registry())
    assert {(item.pattern_id, item.version) for item in pack.exposed_versions} <= {
        ("wave-reveal", 1),
        ("widget-close-technical", 1),
        ("control-hover-technical", 1),
        ("responsive-transition-technical", 1),
        ("background-effect-technical", 1),
    }
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest -q tests/builder_lab_cases/test_pattern_candidate_resolver.py`

- [ ] **Step 3: Implement the immutable stage map and bounded resolver**

```python
STAGE_PATTERN_CATEGORIES = {
    Stage.FOUNDATION: (
        AtomicPatternCategory.LAUNCHER_SHAPE,
        AtomicPatternCategory.SHELL_LAYOUT,
        AtomicPatternCategory.BACKGROUND_EFFECT,
    ),
    Stage.IDENTITY: (
        AtomicPatternCategory.LAUNCHER_ATTENTION,
        AtomicPatternCategory.LAUNCHER_IDLE,
    ),
    Stage.CONVERSATION: (
        AtomicPatternCategory.ASSISTANT_MESSAGE_ENTER,
        AtomicPatternCategory.USER_MESSAGE_ENTER,
        AtomicPatternCategory.TYPING_INDICATOR,
        AtomicPatternCategory.MESSAGE_SEND,
        AtomicPatternCategory.COMPOSER_FOCUS,
    ),
    Stage.MOTION_POLISH: (
        AtomicPatternCategory.WIDGET_OPEN,
        AtomicPatternCategory.WIDGET_CLOSE,
        AtomicPatternCategory.CONTROL_HOVER,
        AtomicPatternCategory.RESPONSIVE_TRANSITION,
        AtomicPatternCategory.BACKGROUND_EFFECT,
    ),
}

@dataclass(frozen=True, slots=True)
class ResolvedPatternCandidatePack:
    stage: Stage
    exposed_versions: tuple[PatternVersionRef, ...]
    prompt_text: str
```

`resolve_pattern_candidate_pack()` rejects any version not present in the persisted shortlist, excludes unrelated categories, validates incompatibilities again and enforces a per-stage UTF-8 byte limit. The prompt includes full `ai_description`, policy, contract, hash, exact html/css/javascript and states that trusted runtime still owns open/close/submit/message insertion.

- [ ] **Step 4: Add optional candidate pack to generation prompts**

```python
def build_stage_prompt(
    *,
    request: BuilderRequest,
    stage: Stage,
    revision: int,
    previous_artifact: WidgetArtifact | None,
    repair_issues: tuple[ValidationIssue, ...] = (),
    visual_findings: tuple[VisualFinding, ...] = (),
    selected_direction: DirectionProposal | None = None,
    composition: ResolvedComposition | None = None,
    pattern_candidate_pack: ResolvedPatternCandidatePack | None = None,
) -> str:
    if composition is not None and pattern_candidate_pack is not None:
        raise ValueError("legacy composition and candidate pack are mutually exclusive")
```

Pass `pattern_candidate_pack` through engine/orchestrator signatures without changing callers that use legacy `composition`.

- [ ] **Step 5: Run prompt/resolver/regression tests and commit**

Run: `python -m pytest -q tests/builder_lab_cases/test_pattern_candidate_resolver.py tests/builder_lab_cases/test_prompts.py tests/builder_lab_cases/test_orchestrator.py`

Expected: PASS.

```text
git add builder_lab/patterns/candidate_resolver.py builder_lab/patterns/atomic_models.py builder_lab/prompts.py builder_lab/engines/base.py builder_lab/engines/gemini_direct.py tests/builder_lab_cases/test_pattern_candidate_resolver.py
git commit -m "feat: inject pattern candidates by stage"
```

### Task 4: PostgreSQL provenance and review persistence

**Files:**

- Create: `migrations/versions/0018_stage_aware_pattern_library.py`
- Create: `app/patterns/candidate_repository.py`
- Create: `tests/saas_cases/test_pattern_candidate_repository.py`
- Create: `tests/saas_cases/test_pattern_candidate_migration.py`
- Modify: `app/saas/models.py`

- [ ] **Step 1: Write failing repository and migration tests**

```python
async def test_candidate_plan_round_trips_after_new_session(session_factory, run, plan, registry):
    async with session_factory() as session:
        repo = PatternCandidateRepository(session)
        await repo.create_plan(
            run_id=run.id,
            plan=plan,
            registry=registry,
            direction_artifact_id=None,
            selector_model_call_id=None,
        )
        await session.commit()
    async with session_factory() as session:
        new_repo = PatternCandidateRepository(session)
        loaded = await new_repo.load_plan(run.id)
    assert loaded.plan == plan

async def test_usage_claim_must_reference_exposed_candidate(repo, run, unexposed_item):
    with pytest.raises(ValueError, match="not exposed"):
        await repo.record_usage_claim(
            run_id=run.id,
            stage=Stage.CONVERSATION,
            candidate_item_id=unexposed_item.id,
            usage_mode="primary",
            model_call_id=None,
        )
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest -q tests/saas_cases/test_pattern_candidate_repository.py tests/saas_cases/test_pattern_candidate_migration.py`

- [ ] **Step 3: Add six additive SQLAlchemy tables**

```python
class PatternCandidatePlanRecord(Base):
    __tablename__ = "pattern_candidate_plans"
    id: Mapped[UUID] = _uuid_pk()
    run_id: Mapped[UUID] = mapped_column(ForeignKey("generation_runs.id", ondelete="CASCADE"), unique=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    direction_id: Mapped[str] = mapped_column(String(80), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    registry_digest: Mapped[str] = mapped_column(String(64), nullable=False)

class PatternCandidateGroupRecord(Base):
    __tablename__ = "pattern_candidate_groups"
    id: Mapped[UUID] = _uuid_pk()
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("pattern_candidate_plans.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(32), nullable=False)

class PatternCandidateItemRecord(Base):
    __tablename__ = "pattern_candidate_items"
    id: Mapped[UUID] = _uuid_pk()
    group_id: Mapped[UUID] = mapped_column(ForeignKey("pattern_candidate_groups.id", ondelete="CASCADE"))
    pattern_version_id: Mapped[UUID] = mapped_column(ForeignKey("widget_pattern_versions.id", ondelete="RESTRICT"))
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)

class PatternStageExposure(Base):
    __tablename__ = "pattern_stage_exposures"
    id: Mapped[UUID] = _uuid_pk()
    run_id: Mapped[UUID] = mapped_column(ForeignKey("generation_runs.id", ondelete="CASCADE"))
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    candidate_item_id: Mapped[UUID] = mapped_column(ForeignKey("pattern_candidate_items.id", ondelete="CASCADE"))

class PatternStageUsageClaim(Base):
    __tablename__ = "pattern_stage_usage_claims"
    id: Mapped[UUID] = _uuid_pk()
    exposure_id: Mapped[UUID] = mapped_column(ForeignKey("pattern_stage_exposures.id", ondelete="CASCADE"))
    usage_mode: Mapped[str] = mapped_column(String(16), nullable=False)

class PatternReview(Base):
    __tablename__ = "pattern_reviews"
    id: Mapped[UUID] = _uuid_pk()
    pattern_version_id: Mapped[UUID] = mapped_column(ForeignKey("widget_pattern_versions.id", ondelete="RESTRICT"))
    reviewer_email: Mapped[str] = mapped_column(String(320), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
```

Use `ondelete="CASCADE"` for run/plan children, `RESTRICT` for immutable pattern versions, check constraints for rank and status, and indexes on run/category/review status. Migration revision is `0018_stage_aware_pattern_library`, down_revision `0017_funnel_journeys`.

- [ ] **Step 4: Implement repository invariants**

`sync_registry()` reuses `widget_pattern_versions`; immutable drift compares the full v3 manifest snapshot and hash. `effective_review_state()` returns latest database review or imported provenance state. `create_plan()` stores plan/groups/items atomically and is idempotent by run. `load_plan()` reconstructs schema v2 without consulting live selector. `record_exposure()` accepts only selected items. `record_usage_claim()` accepts only items exposed to the same run/stage and one of `primary|combined|inspiration`.

- [ ] **Step 5: Verify migration upgrade/downgrade and repository round-trip**

Run: `python -m pytest -q tests/saas_cases/test_pattern_candidate_repository.py tests/saas_cases/test_pattern_candidate_migration.py tests/saas_cases/test_pattern_repository.py`

Expected: PASS and legacy repository remains unchanged.

- [ ] **Step 6: Commit**

```text
git add app/saas/models.py app/patterns/candidate_repository.py migrations/versions/0018_stage_aware_pattern_library.py tests/saas_cases/test_pattern_candidate_repository.py tests/saas_cases/test_pattern_candidate_migration.py
git commit -m "feat: persist stage-aware pattern provenance"
```

### Task 5: Feature-flagged durable worker integration

**Files:**

- Create: `tests/builder_lab_cases/test_pattern_candidate_worker.py`
- Modify: `builder_lab/config.py`
- Modify: `builder_lab/worker.py`
- Modify: `.env.example`

- [ ] **Step 1: Write failing feature-flag and restart tests**

```python
def test_candidate_plan_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("KAIGO_PATTERN_CANDIDATE_PLAN_V2_ENABLED", raising=False)
    assert BuilderLabConfig.from_env().pattern_candidate_plan_v2_enabled is False

async def test_worker_reuses_persisted_candidate_plan_after_restart(worker_fixture):
    run_id = await worker_fixture.seed_candidate_plan()
    worker = worker_fixture.make_worker()
    selector = worker_fixture.selector
    generation_call = worker_fixture.generation_calls
    await worker_fixture.execute_stage(worker, run_id=run_id, stage=Stage.FOUNDATION)
    assert selector.calls == 0
    assert generation_call[-1]["pattern_candidate_pack"].stage is Stage.FOUNDATION
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest -q tests/builder_lab_cases/test_pattern_candidate_worker.py tests/builder_lab_cases/test_config.py`

- [ ] **Step 3: Add the disabled-by-default configuration**

```python
pattern_candidate_plan_v2_enabled: bool
# from_env:
pattern_candidate_plan_v2_enabled=_bool("KAIGO_PATTERN_CANDIDATE_PLAN_V2_ENABLED", False)
```

- [ ] **Step 4: Integrate composition-stage selection and durable resume**

When the flag is false, execute the existing `plan_composition` path byte-for-byte. When true, load an existing candidate plan first; otherwise compute from active/effectively-approved v3 catalog, put it in `StageResult.context`, and persist it in `_materialize_result` before any foundation dispatch. Never call selector again for a persisted run.

- [ ] **Step 5: Resolve and persist each stage exposure**

For foundation/identity/conversation/motion_polish, resolve only that stage pack from the persisted plan, pass it to `BuilderOrchestrator.execute_stage`, and persist exact exposed items/model-call linkage. Repair attempts reuse the identical pack. The generator may report optional structured `pattern_usage_claims`; validate and persist them, but absence of claims does not fail the run.

- [ ] **Step 6: Run worker and persistence suites**

Run: `python -m pytest -q tests/builder_lab_cases/test_pattern_candidate_worker.py tests/builder_lab_cases/test_worker.py tests/saas_cases/test_pattern_worker_persistence.py tests/saas_cases/test_pattern_candidate_repository.py`

Expected: PASS with the feature flag both false and true in focused cases.

- [ ] **Step 7: Commit**

```text
git add builder_lab/config.py builder_lab/worker.py .env.example tests/builder_lab_cases/test_pattern_candidate_worker.py
git commit -m "feat: gate stage-aware pattern generation"
```

### Task 6: Admin-only technical Pattern Lab

**Files:**

- Create: `app/admin/pattern_lab.py`
- Create: `tests/saas_cases/test_pattern_lab.py`
- Modify: `app/admin/routes.py`

- [ ] **Step 1: Write failing auth, sandbox and review tests**

```python
async def test_pattern_lab_redirects_without_admin_session(aiohttp_client, app):
    client = await aiohttp_client(app)
    response = await client.get("/admin/pattern-lab", allow_redirects=False)
    assert response.status == 302
    assert response.headers["Location"] == "/admin/login"

async def test_pattern_lab_preview_is_sandboxed(aiohttp_client, admin_cookie):
    response = await client.get("/admin/pattern-lab")
    html = await response.text()
    assert 'sandbox="allow-scripts"' in html
    assert "allow-same-origin" not in html

async def test_pattern_review_creates_new_review_without_mutating_version(
    aiohttp_client,
    admin_app,
    pattern_version,
):
    client = await aiohttp_client(admin_app)
    before = pattern_version.manifest_snapshot.copy()
    response = await client.post(
        f"/admin/pattern-lab/{pattern_version.pattern_id}/{pattern_version.version}/review",
        data={"status": "approved", "comment": "Контракт проверен"},
    )
    assert response.status in {302, 303}
    assert pattern_version.manifest_snapshot == before
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest -q tests/saas_cases/test_pattern_lab.py`

- [ ] **Step 3: Implement list/detail/preview/review routes**

```python
def setup_pattern_lab_routes(app: web.Application) -> None:
    app.router.add_get("/admin/pattern-lab", pattern_lab_index)
    app.router.add_get("/admin/pattern-lab/{pattern_id}/{version}", pattern_lab_detail)
    app.router.add_get("/admin/pattern-lab/{pattern_id}/{version}/preview", pattern_lab_preview)
    app.router.add_post("/admin/pattern-lab/{pattern_id}/{version}/review", pattern_lab_review)
```

Every handler calls `require_admin_session`. List supports category/status/review filters and shows exact selector-facing `ai_description`. Detail shows contract/policy/hash/provenance and neutral fixture. Preview returns a self-contained document; the parent iframe has exactly `sandbox="allow-scripts"`. Review writes `approved|rejected` plus bounded comment and never edits manifest/assets.

- [ ] **Step 4: Add preview controls without a Studio redesign**

Parent page uses `postMessage` commands `run`, `replay`, `open`, `close`, `assistant-message`, `user-message`, `typing`, `desktop`, `mobile`. Preview validates a fixed command allowlist and exposes no network/storage API. Empty categories render an explanatory state rather than an error.

- [ ] **Step 5: Run admin tests and commit**

Run: `python -m pytest -q tests/saas_cases/test_pattern_lab.py tests/saas_cases/test_funnel_admin.py tests/saas_cases/test_generation_forensics_admin.py`

Expected: PASS.

```text
git add app/admin/pattern_lab.py app/admin/routes.py tests/saas_cases/test_pattern_lab.py
git commit -m "feat: add technical pattern lab"
```

### Task 7: Full technical regression, documentation and completion audit

**Files:**

- Modify only if cleanly mergeable: `docs/product-journal/2026-08.md`
- Create: `docs/telegram/release-packets/2026-08-05-stage-aware-pattern-library.md`
- Modify only if cleanly mergeable: `docs/telegram/content-backlog.md`

- [ ] **Step 1: Run all new focused suites**

Run:

```text
python -m pytest -q tests/builder_lab_cases/test_atomic_pattern_registry.py tests/builder_lab_cases/test_pattern_candidate_planner.py tests/builder_lab_cases/test_pattern_candidate_resolver.py tests/builder_lab_cases/test_pattern_candidate_worker.py tests/saas_cases/test_pattern_candidate_repository.py tests/saas_cases/test_pattern_candidate_migration.py tests/saas_cases/test_pattern_lab.py
```

Expected: PASS.

- [ ] **Step 2: Run required legacy/regression suites**

Run:

```text
python -m pytest -q tests/builder_lab_cases/test_pattern_registry.py tests/builder_lab_cases/test_pattern_planner.py tests/builder_lab_cases/test_pattern_catalog_v2.py tests/builder_lab_cases/test_pattern_resolver.py tests/builder_lab_cases/test_prompts.py tests/builder_lab_cases/test_worker.py tests/saas_cases/test_pattern_repository.py tests/saas_cases/test_pattern_worker_persistence.py tests/saas_cases/test_pattern_outcome_publication.py
```

Expected: PASS. A paid live model run and manual visual acceptance are explicitly out of scope.

- [ ] **Step 3: Verify migration chain and static hygiene**

Run:

```text
python -m compileall -q builder_lab app
python -m alembic heads
git diff --check
```

Expected: compile success, one Alembic head `0018_stage_aware_pattern_library`, no whitespace errors.

- [ ] **Step 4: Self-review against all 13 design checks**

Record evidence for: full `ai_description`; no assets in selector; 2–5 shortlist; server rejection; fallback; restart; stage mapping; exact versions; usage-claim restriction; legacy compatibility; migration round-trip; Pattern Lab auth/sandbox; worker/provider regression.

- [ ] **Step 5: Update the editorial contour without overwriting dirty user files**

Create a Russian release packet explaining confirmed technical capability and clearly label production activation/catalog population as future work. If `docs/product-journal/2026-08.md` or `docs/telegram/content-backlog.md` still has unrelated user changes, do not stage or rewrite it; report the skipped append instead.

- [ ] **Step 6: Review scoped diff and commit**

```text
git status --short
git diff --check
git diff --stat HEAD
git add <only stage-aware pattern library files>
git commit -m "feat: complete stage-aware pattern library"
```

- [ ] **Step 7: Clean completed subagent storage and report**

Use the `clean-subagent-storage` skill after all delegated tasks are closed. Do not delete active or unrelated user-owned tasks. Report exact commits, tests, feature-flag state, Pattern Lab route and the remaining visual catalog-population work.

## Self-review result

- Spec coverage: all 13 mandatory checks and all 8 readiness criteria map to Tasks 1–7.
- Compatibility: legacy manifests, plans, worker path and outcomes remain intact behind a default-off flag.
- Scope: no pgvector, vector service, public Studio redesign, paid live generation or artistic 20-pattern population was introduced.
- Persistence semantics: selected, exposed and claimed-used are separate; `verified_effect` remains an explicitly future signal rather than being inferred.
- Placeholder scan: every implementation step names its concrete API, command and expected outcome.
