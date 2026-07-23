# Kaigo Contract and A/B/C Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Создать расширяемый `chat-v1` контракт, последовательные специализированные
роли, три Direct-профиля A/B/C, одну строгую визуальную ревизию и публичное сравнение
raw/final для RAW BUREAU.

**Architecture:** `BuilderRequest` выбирает версионируемый контракт и творческий профиль.
Последовательный direction pipeline передаёт результат аналитика conversation designer,
а затем art director. Существующий Direct runtime и deterministic BrowserAudit остаются
общими для всех профилей. Visual gate хранит исходный кандидат и допускает ровно одну
модельную ревизию, после чего экспериментальный runner формирует самостоятельные демо
и comparison manifest.

**Tech Stack:** Python 3.11+, dataclasses, unittest/pytest, Google GenAI SDK,
Playwright Chromium, aiohttp, HTML/CSS/JavaScript, Nginx, Docker.

---

### Task 1: Версионируемый контракт и творческие профили

**Files:**
- Create: `builder_lab/contracts.py`
- Modify: `builder_lab/models.py`
- Modify: `builder_lab/prompts.py`
- Create: `tests/builder_lab_cases/test_contracts.py`
- Modify: `tests/builder_lab_cases/test_models.py`
- Modify: `tests/builder_lab_cases/test_directions.py`

- [ ] **Step 1: Write failing contract and serialization tests**

```python
def test_chat_v1_contract_exposes_runtime_and_motion_invariants():
    contract = resolve_widget_contract("chat-v1")
    assert contract.contract_id == "chat-v1"
    assert contract.attention_delay_seconds == 15
    assert "prefers-reduced-motion" in contract.prompt_block
    assert "data-region=\"launcher\"" in contract.prompt_block


def test_builder_request_round_trips_contract_and_profile():
    request = BuilderRequest(
        engine=EngineName.DIRECT,
        brief="Собери чат",
        contract_id="chat-v1",
        creative_profile=CreativeProfile.BRAND_MOTION,
        visual_repair_limit=1,
    )
    assert BuilderRequest.from_dict(request.to_dict()) == request
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
python -m pytest tests/builder_lab_cases/test_contracts.py tests/builder_lab_cases/test_models.py -q
```

Expected: imports for `contracts` and `CreativeProfile` fail because they do not exist.

- [ ] **Step 3: Implement the minimal contract registry**

```python
class CreativeProfile(str, Enum):
    PRODUCT_CHAT = "product_chat"
    BRAND_MOTION = "brand_motion"
    AI_CHARACTER = "ai_character"


@dataclass(frozen=True)
class WidgetContract:
    contract_id: str
    version: int
    attention_delay_seconds: int
    prompt_block: str
    required_regions: tuple[str, ...]


WIDGET_CONTRACTS = {"chat-v1": CHAT_V1}


def resolve_widget_contract(contract_id: str) -> WidgetContract:
    try:
        return WIDGET_CONTRACTS[contract_id]
    except KeyError as exc:
        raise ValueError(f"unsupported widget contract: {contract_id}") from exc
```

Add `contract_id`, `creative_profile`, and `visual_repair_limit` to
`BuilderRequest`, validate them, and include them in `to_dict`/`from_dict`.

- [ ] **Step 4: Make prompts consume contract and profile**

`build_direction_proposal_prompt` and `build_stage_prompt` must resolve
`request.contract_id`, include `contract.prompt_block`, and include exactly one profile
brief:

```python
PROFILE_PROMPTS = {
    CreativeProfile.PRODUCT_CHAT: "...readability and familiar conversation...",
    CreativeProfile.BRAND_MOTION: "...maximum brand expression and motion carte blanche...",
    CreativeProfile.AI_CHARACTER: "...digital employee or character across launcher and states...",
}
```

Do not add fixed colors, a fixed avatar, or one exact panel size.

- [ ] **Step 5: Run focused tests and verify GREEN**

```powershell
python -m pytest tests/builder_lab_cases/test_contracts.py tests/builder_lab_cases/test_models.py tests/builder_lab_cases/test_directions.py -q
```

- [ ] **Step 6: Commit**

```powershell
git add builder_lab/contracts.py builder_lab/models.py builder_lab/prompts.py tests/builder_lab_cases/test_contracts.py tests/builder_lab_cases/test_models.py tests/builder_lab_cases/test_directions.py
git commit -m "feat: add versioned widget contracts"
```

### Task 2: Последовательные специализированные роли

**Files:**
- Create: `builder_lab/concept_roles.py`
- Create: `tests/builder_lab_cases/test_concept_roles.py`
- Modify: `builder_lab/models.py`
- Modify: `builder_lab/engines/base.py`
- Modify: `builder_lab/engines/gemini_direct.py`
- Modify: `builder_lab/prompts.py`
- Modify: `tests/builder_lab_cases/test_gemini_direct.py`

- [ ] **Step 1: Write a failing sequence test**

```python
async def test_direction_roles_run_sequentially_and_receive_prior_work():
    engine = RecordingConceptRoleEngine()
    result = await run_concept_roles(engine=engine, request=request())
    assert engine.calls == [
        ("site_brand_analyst", ()),
        ("conversation_designer", ("site_brand_analyst",)),
        (
            "art_director_frontend_developer",
            ("site_brand_analyst", "conversation_designer"),
        ),
    ]
    assert result.selected_direction.role == "art_director_frontend_developer"
```

Also assert that failure usage includes only completed role calls and that a failed role
prevents later calls.

- [ ] **Step 2: Run and verify RED**

```powershell
python -m pytest tests/builder_lab_cases/test_concept_roles.py tests/builder_lab_cases/test_gemini_direct.py -q
```

Expected: `concept_roles` and its bounded models do not exist.

- [ ] **Step 3: Add bounded experiment-only role models**

```python
class ConceptRole(str, Enum):
    SITE_BRAND_ANALYST = "site_brand_analyst"
    CONVERSATION_DESIGNER = "conversation_designer"
    ART_DIRECTOR_FRONTEND_DEVELOPER = "art_director_frontend_developer"


@dataclass(frozen=True)
class ConceptRoleBrief:
    role: ConceptRole
    summary: str
    decisions: tuple[str, ...]
    safeguards: tuple[str, ...]
```

Do not change the existing `DirectionRole`, `DIRECTION_ROLES`,
`run_direction_board`, blind judge or legacy Direct path.

- [ ] **Step 4: Pass prior proposals through the engine**

Add an experiment-only engine method:

```python
async def develop_concept_role(
    *,
    request: BuilderRequest,
    role: ConceptRole,
    prior_briefs: tuple[ConceptRoleBrief, ...] = (),
) -> ConceptRoleResult: ...
```

The provider prompt serializes prior briefs as untrusted data. The analyst receives
none, conversation designer receives analyst output, art director receives both. Only
the final role gets the creative-profile block; the canonical site and conversation
inputs remain the same for A/B/C.

- [ ] **Step 5: Replace blind selection with final synthesis**

Run the new roles sequentially and convert the third bounded brief to one
`DirectionProposal` compatible with the existing stage generator. Do not make a fourth
model call. Existing `run_direction_board` continues to serve legacy runs unchanged.

- [ ] **Step 6: Run focused tests and verify GREEN**

```powershell
python -m pytest tests/builder_lab_cases/test_concept_roles.py tests/builder_lab_cases/test_directions.py tests/builder_lab_cases/test_gemini_direct.py tests/builder_lab_cases/test_orchestrator.py -q
```

- [ ] **Step 7: Commit**

```powershell
git add builder_lab/concept_roles.py builder_lab/models.py builder_lab/engines/base.py builder_lab/engines/gemini_direct.py builder_lab/prompts.py tests/builder_lab_cases/test_concept_roles.py tests/builder_lab_cases/test_gemini_direct.py
git commit -m "feat: split direction work into sequential roles"
```

### Task 3: Runtime lifecycle и attention motion

**Files:**
- Modify: `builder_lab/preview.py`
- Modify: `builder_lab/browser_audit.py`
- Modify: `tests/builder_lab_cases/test_preview.py`
- Modify: `tests/builder_lab_cases/test_browser_audit.py`

- [ ] **Step 1: Write failing lifecycle tests**

```python
def test_runtime_has_separate_pending_and_failed_requests():
    document = render_widget_document(artifact())
    assert "pendingRequest" in document
    assert "failedRequest" in document
    assert "pendingRequest = null" in document


def test_runtime_attention_is_one_shot_and_reduced_motion_safe():
    document = render_widget_document(artifact())
    assert "15000" in document
    assert "kaigo-preview-attention" in document
    assert "prefers-reduced-motion: reduce" in document
```

Add browser cases proving that a failed request does not leave a visually enabled but
non-working send control, retry does not duplicate the user message, and new send from
error creates a new request.

- [ ] **Step 2: Run and verify RED**

```powershell
python -m pytest tests/builder_lab_cases/test_preview.py tests/builder_lab_cases/test_browser_audit.py -q
```

- [ ] **Step 3: Implement explicit request state**

Use separate `pendingRequest` and `failedRequest`. On transport error, move the request
to `failedRequest` and clear pending. Retry reuses the failed request without adding a
second user message. New send removes the old error and creates a new request.

- [ ] **Step 4: Implement server-owned attention state**

The runtime schedules one 15-second timer only while the document is visible, the
surface is closed and the user has not interacted. It toggles
`.kaigo-preview-attention`, never opens the panel, never moves focus and never plays
sound. Open, pointer, key, visibility change or reduced-motion cancels it for the page
session. Generated CSS owns the visual response.

- [ ] **Step 5: Add separate motion probes**

Keep the existing deterministic layout flow motion-frozen. Add:

- `no-preference` attention probe;
- reduced-motion probe;
- assertions for no auto-open, no focus change and one-shot cancellation.

- [ ] **Step 6: Run focused tests and verify GREEN**

```powershell
python -m pytest tests/builder_lab_cases/test_preview.py tests/builder_lab_cases/test_browser_audit.py -q
```

- [ ] **Step 7: Commit**

```powershell
git add builder_lab/preview.py builder_lab/browser_audit.py tests/builder_lab_cases/test_preview.py tests/builder_lab_cases/test_browser_audit.py
git commit -m "feat: add contract driven widget lifecycle"
```

### Task 4: Агрессивный критик, raw candidate и одна ревизия

**Files:**
- Create: `builder_lab/strict_visual_models.py`
- Create: `builder_lab/strict_visual_critic.py`
- Create: `builder_lab/experiment_review.py`
- Create: `tests/builder_lab_cases/test_strict_visual_models.py`
- Create: `tests/builder_lab_cases/test_strict_visual_critic.py`
- Create: `tests/builder_lab_cases/test_experiment_review.py`

- [ ] **Step 1: Write failing typed-rubric tests**

```python
def test_host_rejects_score_below_release_bar():
    critique = StrictVisualCritique(assessments=assessments(score=3), ...)
    assert critique.verdict is StrictVisualVerdict.REPAIR


def test_core_dimension_below_four_blocks_even_high_average():
    critique = StrictVisualCritique(
        assessments=assessments(default=5, conversation_clarity=3),
        ...
    )
    assert critique.verdict is StrictVisualVerdict.REPAIR
```

Require exactly ten dimensions:
`direction_fidelity`, `page_subordination`, `visual_hierarchy`,
`conversation_clarity`, `typography_legibility`, `spacing_alignment`,
`system_coherence`, `responsive_composition`, `craft_polish`, `distinctiveness`.
Score `0` means `not_observable`; every observable score has confidence at least `0.80`;
every score `1..3` has a concrete linked finding.

- [ ] **Step 2: Write failing adversarial prompt tests**

```python
prompt = build_strict_visual_critic_prompt(locale="ru", phase="raw")
assert "не пишешь дружеский feedback" in prompt
assert "не выдумывай дефект" in prompt
assert "двухсекунд" in prompt
assert "микродетал" in prompt
assert "не возвращай verdict" in prompt
```

The model returns observations, assessments, findings and a revision plan, but never its
own pass/fail verdict. Python derives the verdict.

- [ ] **Step 3: Write failing one-revision state-machine tests**

```python
async def test_repair_then_pass_uses_exactly_one_generation():
    result = await reviewer.review(raw_artifact)
    assert engine.visual_revision_calls == 1
    assert auditor.calls == 2
    assert critic.calls == 2
    assert result.raw.artifact == raw_artifact
    assert result.final.verdict == "pass"


async def test_second_failure_is_terminal_without_another_generation():
    with pytest.raises(ExperimentVisualQualityError):
        await reviewer.review(raw_artifact)
    assert engine.visual_revision_calls == 1
```

Initial deterministic failure calls neither taste critic nor generator. A deterministic
regression after the one revision is terminal.

- [ ] **Step 4: Run and verify RED**

```powershell
python -m pytest tests/builder_lab_cases/test_strict_visual_models.py tests/builder_lab_cases/test_strict_visual_critic.py tests/builder_lab_cases/test_experiment_review.py -q
```

- [ ] **Step 5: Implement the experiment-only strict critic**

Require concrete negative inspection across:

- two-second recognition as chat;
- user/AI separation and authorship;
- typographic readability and density;
- first-open cognitive load;
- composer and launcher discoverability;
- mobile subordination to the page;
- brand specificity versus generic SaaS/AI styling;
- fake or decorative actions;
- small alignment, wrapping, contrast and spacing defects.

Forbid generic standalone language such as “looks clean”, “можно улучшить иерархию”
and “add breathing room”. Keep screenshot-specific observations and pixel proof. The
model may return zero findings when evidence supports a strong result.

- [ ] **Step 6: Compute release verdict in Python**

Use:

```python
repair = (
    weighted_score < 4.0
    or any(core_score < 4 for core_score in core_scores)
    or any(score <= 2 for score in observable_scores)
)
```

Severity derives from score: `1=blocker`, `2=major`, `3=minor`. The model cannot lower
confidence to avoid a repair. `not_observable` is uncertainty, not a defect.

- [ ] **Step 7: Implement immutable raw/final review records**

`ExperimentReviewResult` stores raw artifact, raw audit, raw critique, final artifact,
final audit, final critique, usage and elapsed time. The raw record is created once and
never overwritten. The sole visual revision may change only fields allowed by at most
three revision actions; unrelated redesign is rejected.

- [ ] **Step 8: Run focused tests and verify GREEN**

```powershell
python -m pytest tests/builder_lab_cases/test_strict_visual_models.py tests/builder_lab_cases/test_strict_visual_critic.py tests/builder_lab_cases/test_experiment_review.py -q
```

- [ ] **Step 9: Confirm legacy gate is unchanged**

```powershell
python -m pytest tests/builder_lab_cases/test_visual_models.py tests/builder_lab_cases/test_visual_critic.py tests/builder_lab_cases/test_visual_repair_gate.py -q
```

- [ ] **Step 10: Commit**

```powershell
git add builder_lab/strict_visual_models.py builder_lab/strict_visual_critic.py builder_lab/experiment_review.py tests/builder_lab_cases/test_strict_visual_models.py tests/builder_lab_cases/test_strict_visual_critic.py tests/builder_lab_cases/test_experiment_review.py
git commit -m "feat: add adversarial one-pass visual review"
```

### Task 5: A/B/C experiment runner and comparison package

**Files:**
- Create: `builder_lab/experiments.py`
- Create: `scripts/run_abc_comparison.py`
- Create: `tests/builder_lab_cases/test_experiments.py`
- Modify: `builder_lab/comparison.py`
- Modify: `tests/builder_lab_cases/test_comparison.py`

- [ ] **Step 1: Write failing manifest tests**

```python
def test_experiment_manifest_requires_three_unique_profiles():
    manifest = AbcExperimentManifest.create(
        source_digest="a" * 64,
        contract_id="chat-v1",
        variants=(variant_a, variant_b, variant_c),
    )
    assert [item.profile.value for item in manifest.variants] == [
        "product_chat", "brand_motion", "ai_character"
    ]


def test_variant_keeps_raw_and_final_separate():
    assert variant.raw.artifact.revision == 5
    assert variant.final.artifact.revision == 5
    assert variant.raw.artifact != variant.final.artifact
```

- [ ] **Step 2: Run and verify RED**

```powershell
python -m pytest tests/builder_lab_cases/test_experiments.py tests/builder_lab_cases/test_comparison.py -q
```

- [ ] **Step 3: Implement bounded experiment models**

```python
@dataclass(frozen=True)
class ExperimentEvidence:
    artifact: WidgetArtifact
    audit: BrowserAuditReport
    critique: VisualCritique | None


@dataclass(frozen=True)
class ExperimentVariant:
    profile: CreativeProfile
    raw: ExperimentEvidence
    final: ExperimentEvidence
    usage: TokenUsage
    elapsed_seconds: float


@dataclass(frozen=True)
class AbcExperimentManifest:
    source_digest: str
    contract_id: str
    variants: tuple[ExperimentVariant, ...]
```

Validate exactly A/B/C, one source digest, one contract, unique public slugs and bounded
payloads.

- [ ] **Step 4: Implement the runner**

`run_abc_comparison.py`:

1. Loads one frozen RAW BUREAU bundle.
2. Starts three requests concurrently with `asyncio.gather`.
3. Uses a semaphore of one for Chromium audits.
4. Waits for terminal snapshots.
5. Reads raw and final candidates from the stores.
6. Saves demos, screenshots, critiques, role events, usage and manifest.
7. Never overwrites v1 or Direct Chat v2 directories.

- [ ] **Step 5: Render raw/final comparison**

Extend comparison rendering with optional raw/final pairs, profile label, critique
summary, elapsed seconds and token/cost fields. Existing callers without these fields
must render unchanged.

- [ ] **Step 6: Run focused tests and verify GREEN**

```powershell
python -m pytest tests/builder_lab_cases/test_experiments.py tests/builder_lab_cases/test_comparison.py -q
```

- [ ] **Step 7: Commit**

```powershell
git add builder_lab/experiments.py builder_lab/comparison.py scripts/run_abc_comparison.py tests/builder_lab_cases/test_experiments.py tests/builder_lab_cases/test_comparison.py
git commit -m "feat: package direct abc experiments"
```

### Task 6: Regression verification before paid generation

**Files:**
- No production changes expected

- [ ] **Step 1: Run all non-browser builder tests**

```powershell
python -m pytest tests/builder_lab_cases -q -k "not real_chromium"
```

Expected: zero failures.

- [ ] **Step 2: Run real Chromium contract test**

```powershell
python -m pytest tests/builder_lab_cases/test_browser_audit.py -k real_chromium_captures_six_jpegs_and_eight_layout_states -q
```

Expected: one passing real Chromium test.

- [ ] **Step 3: Verify repository hygiene**

```powershell
git diff --check
git status --short --branch
```

### Task 7: Generate and publish RAW BUREAU A/B/C

**Files:**
- Create: `docs/RAW_BUREAU_ABC_COMPARISON.md`
- Create: `docs/evidence/raw-bureau-abc/*`
- Server output: `/var/www/kaigo-builder-comparison/direct-abc-v1/`

- [ ] **Step 1: Deploy source without replacing old demos**

Push the branch, fast-forward `/root/ai_project`, rebuild a separately named experiment
container, run `nginx -t`, and preserve all existing routes.

- [ ] **Step 2: Execute the paid A/B/C runner**

Use `gemini-3.6-flash`, maximum supported thinking, the same frozen RAW BUREAU digest,
`chat-v1`, and `visual_repair_limit=1`. Record failed variants honestly rather than
silently substituting another profile.

- [ ] **Step 3: Inspect raw and final screenshots**

Review all desktop/mobile after-turn screenshots. Confirm that:

- A is the clearest product chat;
- B is genuinely more expressive in shape and motion;
- C visibly uses a character or digital employee;
- none violates the shared contract.

- [ ] **Step 4: Publish separate interactive demos and comparison**

Expected routes:

```text
/builder-comparison/direct-abc-v1/
/builder-comparison/direct-abc-v1/product-chat/
/builder-comparison/direct-abc-v1/brand-motion/
/builder-comparison/direct-abc-v1/ai-character/
```

Raw evidence may be viewed from comparison, but only accepted final artifacts receive
interactive chat routes.

- [ ] **Step 5: Verify in the in-app Browser**

For every accepted final:

1. Open the widget.
2. Send two real questions.
3. Confirm AI-left/user-right and hidden quick replies.
4. Close and reopen with history preserved.
5. Switch to mobile and repeat one question.
6. Check console errors.

- [ ] **Step 6: Save evidence and report**

Copy the manifest, prompts, critiques and selected screenshots into
`docs/evidence/raw-bureau-abc/`. Document exact cost in USD and RUB, including failed
paid runs.

- [ ] **Step 7: Final verification, GitHub and server sync**

Run the relevant test suite again, commit documentation/evidence, push, fast-forward the
server checkout, verify remote SHA, HTTP 200 routes, Nginx and container health.
