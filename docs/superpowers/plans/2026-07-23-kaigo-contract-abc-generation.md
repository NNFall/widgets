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
- Modify: `builder_lab/models.py`
- Modify: `builder_lab/directions.py`
- Modify: `builder_lab/engines/base.py`
- Modify: `builder_lab/engines/gemini_direct.py`
- Modify: `builder_lab/prompts.py`
- Modify: `tests/builder_lab_cases/test_directions.py`
- Modify: `tests/builder_lab_cases/test_gemini_direct.py`
- Modify: `tests/builder_lab_cases/test_orchestrator.py`

- [ ] **Step 1: Write a failing sequence test**

```python
async def test_direction_roles_run_sequentially_and_receive_prior_work():
    engine = RecordingDirectionEngine()
    result = await run_direction_board(engine=engine, request=request())
    assert engine.calls == [
        ("site_brand_analyst", ()),
        ("conversation_designer", ("candidate-1",)),
        ("art_director_frontend_developer", ("candidate-1", "candidate-2")),
    ]
    assert result.selected.proposal_id == "candidate-3"
```

Also assert that failure usage includes only completed role calls and that a failed role
prevents later calls.

- [ ] **Step 2: Run and verify RED**

```powershell
python -m pytest tests/builder_lab_cases/test_directions.py tests/builder_lab_cases/test_gemini_direct.py -q
```

Expected: the existing board runs three proposals in parallel and invokes a blind judge.

- [ ] **Step 3: Add the new role vocabulary**

```python
class DirectionRole(str, Enum):
    SITE_BRAND_ANALYST = "site_brand_analyst"
    CONVERSATION_DESIGNER = "conversation_designer"
    ART_DIRECTOR_FRONTEND_DEVELOPER = "art_director_frontend_developer"
```

Keep legacy enum values readable for historical snapshots, but do not include them in
the new `DIRECTION_ROLES`.

- [ ] **Step 4: Pass prior proposals through the engine**

Extend `propose_direction` with:

```python
prior_proposals: tuple[DirectionProposal, ...] = ()
```

The provider prompt serializes prior proposals as untrusted data. The analyst receives
none, conversation designer receives analyst output, art director receives both.

- [ ] **Step 5: Replace blind selection with final synthesis**

Run roles sequentially. `DirectionBoardResult.selected` returns the third proposal.
Create a local `DirectionJudgement` selecting `candidate-3` with a bounded rationale.
Do not make a fourth model call. Keep the legacy `judge_directions` method only for
reading old code paths until a separate cleanup.

- [ ] **Step 6: Run focused tests and verify GREEN**

```powershell
python -m pytest tests/builder_lab_cases/test_directions.py tests/builder_lab_cases/test_gemini_direct.py tests/builder_lab_cases/test_orchestrator.py -q
```

- [ ] **Step 7: Commit**

```powershell
git add builder_lab/models.py builder_lab/directions.py builder_lab/engines/base.py builder_lab/engines/gemini_direct.py builder_lab/prompts.py tests/builder_lab_cases/test_directions.py tests/builder_lab_cases/test_gemini_direct.py tests/builder_lab_cases/test_orchestrator.py
git commit -m "feat: split direction work into sequential roles"
```

### Task 3: Агрессивный критик, raw candidate и одна ревизия

**Files:**
- Modify: `builder_lab/visual_critic.py`
- Modify: `builder_lab/visual_gate.py`
- Modify: `builder_lab/store.py`
- Modify: `tests/builder_lab_cases/test_visual_critic.py`
- Modify: `tests/builder_lab_cases/test_visual_repair_gate.py`
- Modify: `tests/builder_lab_cases/test_store.py`

- [ ] **Step 1: Write failing critic prompt tests**

```python
assert "Act as an adversarial independent design QA" in system_instruction
assert "Do not praise" in system_instruction
assert "smallest observable defect" in system_instruction
assert "do not invent defects" in system_instruction
assert "two-second chat recognition" in system_instruction
```

The existing screenshot-specificity and pixel-proof requirements must remain.

- [ ] **Step 2: Write failing one-revision and raw-preservation tests**

```python
async def test_visual_gate_performs_at_most_request_visual_repair_limit():
    request = make_request(visual_repair_limit=1)
    with pytest.raises(BuilderEngineError):
        await gate.evaluate(...)
    assert engine.visual_repair_calls == 1


async def test_store_preserves_first_visual_candidate():
    await store.stage_visual_candidate(run_id, raw)
    await store.stage_visual_candidate(run_id, repaired)
    assert await store.visual_initial_candidate(run_id) == raw
    assert await store.visual_candidate(run_id) == repaired
```

- [ ] **Step 3: Run and verify RED**

```powershell
python -m pytest tests/builder_lab_cases/test_visual_critic.py tests/builder_lab_cases/test_visual_repair_gate.py tests/builder_lab_cases/test_store.py -q
```

- [ ] **Step 4: Harden the critic instruction**

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

Keep the evidence rule: no finding without screenshot or deterministic metric.

- [ ] **Step 5: Parameterize the repair loop**

Replace the global repair ceiling inside `evaluate` with:

```python
repair_limit = request.visual_repair_limit
if repair_count >= repair_limit:
    raise self._quality_error("visual_repair_exhausted", usage=total_usage)
```

Browser/runtime repair remains separate from the single model-taste revision.

- [ ] **Step 6: Preserve raw visual candidate**

Add `_RunRecord.visual_initial_artifact`, store the first staged candidate once, and
expose `visual_initial_candidate(run_id)`. Return defensive copies exactly like the
existing artifact APIs.

- [ ] **Step 7: Run focused tests and verify GREEN**

```powershell
python -m pytest tests/builder_lab_cases/test_visual_critic.py tests/builder_lab_cases/test_visual_repair_gate.py tests/builder_lab_cases/test_store.py -q
```

- [ ] **Step 8: Commit**

```powershell
git add builder_lab/visual_critic.py builder_lab/visual_gate.py builder_lab/store.py tests/builder_lab_cases/test_visual_critic.py tests/builder_lab_cases/test_visual_repair_gate.py tests/builder_lab_cases/test_store.py
git commit -m "feat: add adversarial one-pass visual review"
```

### Task 4: A/B/C experiment runner and comparison package

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

### Task 5: Regression verification before paid generation

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

### Task 6: Generate and publish RAW BUREAU A/B/C

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

