# Specialist Visual Findings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve strong specialist findings, let an image-seeing judge independently confirm one-critic evidence, and measure Luna Medium/High/Max on three frozen problem widgets.

**Architecture:** The existing three-role committee stays parallel. Each critic returns the shared checks plus a role-specific scorecard. The judge receives the original widget frames and records one of three confirmation bases for every accepted repair: cross-critic consensus, independent judge confirmation, or deterministic browser fact. Codex bridge reasoning becomes a bounded per-turn request field so visual actors can be benchmarked without changing the global builder default.

**Tech Stack:** Python 3.12, pytest, asyncio, JSON Schema, aiohttp/httpx, Codex CLI bridge, existing Kaigo BrowserAudit and visual-repair gate.

---

### Task 1: Per-turn Codex reasoning effort

**Files:**
- Modify: `tests/saas_cases/test_codex_bridge_provider.py`
- Modify: `tests/codex_bridge_cases/test_service.py`
- Modify: `tests/codex_bridge_cases/test_runner.py`
- Modify: `app/models/providers/codex_bridge.py`
- Modify: `tools/kaigo_codex_bridge/service.py`
- Modify: `tools/kaigo_codex_bridge/runner.py`

- [ ] **Step 1: Write failing provider, service and runner tests**

Add tests proving that metadata `thinking_level="high"` becomes the allowlisted
bridge field `reasoning_effort="high"`, that `max` is accepted only as an explicit
bridge request, invalid values return HTTP 400, and the runner command uses the
request value instead of the global default.

- [ ] **Step 2: Run tests and verify the expected RED result**

Run:

```powershell
python -m pytest -q tests/saas_cases/test_codex_bridge_provider.py tests/codex_bridge_cases/test_service.py tests/codex_bridge_cases/test_runner.py
```

Expected: failures showing that `reasoning_effort` is absent from the payload and
`CodexTurnRequest`.

- [ ] **Step 3: Implement the bounded protocol field**

Add `reasoning_effort: str | None` to `CodexTurnRequest`, allow only
`low|medium|high|xhigh|max` in the bridge service, serialize normalized visual
`thinking_level` from the provider, and select:

```python
reasoning_effort = request.reasoning_effort or self.config.reasoning_effort
```

when building the Codex CLI command. Do not mutate the bridge global default.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the command from Step 2. Expected: all selected tests pass.

### Task 2: Specialist scorecards

**Files:**
- Modify: `tests/builder_lab_cases/test_visual_critic.py`
- Modify: `builder_lab/visual_critic.py`

- [ ] **Step 1: Write failing tests for distinct role scorecards**

Assert that every result contains all ten shared checks and exactly these eight
checks for its role:

```python
ROLE_SPECIALIST_CHECKS = {
    VisualCriticRole.CONVERSATION_UX: (
        "transcript_visibility_and_order",
        "message_authorship_separation",
        "composer_text_alignment",
        "scroll_and_history_continuity",
        "copy_wrap_and_readability",
        "first_open_information_density",
        "quick_reply_interaction_value",
        "desktop_mobile_task_parity",
    ),
    VisualCriticRole.BRAND_MOTION: (
        "art_direction_fidelity",
        "brand_specificity",
        "typography_hierarchy",
        "color_material_coherence",
        "spacing_and_alignment_craft",
        "launcher_distinctiveness",
        "panel_visual_rhythm",
        "premium_finish",
    ),
    VisualCriticRole.ADVERSARIAL_CUSTOMER: (
        "first_click_invitation",
        "immediate_value_clarity",
        "trust_and_credibility",
        "copy_naturalness",
        "cognitive_load",
        "task_start_clarity",
        "perceived_usefulness",
        "willingness_to_keep_or_pay",
    ),
}
```

Also test that a failed specialist check with score at most six must cite supplied
screenshots and link to a finding with the same stable `issue_type`.

- [ ] **Step 2: Run the critic suite and verify RED**

```powershell
python -m pytest -q tests/builder_lab_cases/test_visual_critic.py
```

Expected: the result/schema has no `specialist_checks` field.

- [ ] **Step 3: Implement the scorecard contract**

Add a bounded `specialist_checks` array to the provider schema and
`VisualCriticResult`. Reuse the structured-check evidence fields, validate the
exact role set locally, and replace the short role descriptions with explicit
inspection instructions. Keep still-image motion claims forbidden.

- [ ] **Step 4: Run the critic suite and verify GREEN**

Run the command from Step 2. Expected: all tests pass.

### Task 3: One specialist plus independent judge confirmation

**Files:**
- Modify: `tests/builder_lab_cases/test_visual_review.py`
- Modify: `tests/builder_lab_cases/test_visual_committee.py`
- Modify: `builder_lab/visual_review.py`
- Modify: `builder_lab/visual_committee.py`

- [ ] **Step 1: Write failing judgement tests**

Add three cases:

```python
def test_judge_accepts_one_specialist_when_it_inspected_supplied_frame(): ...
def test_judge_rejects_one_specialist_without_visual_confirmation(): ...
def test_judge_preserves_cross_critic_and_deterministic_confirmation(): ...
```

The accepted judge finding must include exactly one `confirmation_basis` from:

```python
{"cross_critic_consensus", "judge_visual_confirmation", "deterministic_fact"}
```

For `judge_visual_confirmation`, require one eligible source, an exact supplied
screenshot ID and `judge_inspected_images=True`. A text-only judge must not be able
to claim independent confirmation.

- [ ] **Step 2: Run the review and committee suites and verify RED**

```powershell
python -m pytest -q tests/builder_lab_cases/test_visual_review.py tests/builder_lab_cases/test_visual_committee.py
```

Expected: the schema rejects `confirmation_basis` and the validator still requires
two roles.

- [ ] **Step 3: Implement confirmation bases and restore judge frames**

Extend `VISUAL_JUDGE_SCHEMA`, `VisualJudgeResult` and `VisualCommitteeResult` with
confirmation metadata. Route the five canonical widget frames to the judge again.
Validation rules:

```text
cross_critic_consensus -> at least two distinct eligible roles
judge_visual_confirmation -> at least one eligible role and judge images supplied
deterministic_fact -> server-owned fact for the exact issue_type
```

Keep unknown screenshot, unsupported scope, JavaScript and static-motion guards.
Update the judge prompt to inspect every single-specialist finding rather than drop
it automatically.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2. Expected: all tests pass.

### Task 4: Preserve confirmation evidence through repair

**Files:**
- Modify: `tests/builder_lab_cases/test_visual_repair_gate.py`
- Modify: `tests/builder_lab_cases/test_prompts.py`
- Modify: `builder_lab/visual_gate.py`
- Modify: `builder_lab/prompts.py`

- [ ] **Step 1: Write failing forensic and repair-prompt tests**

Assert that the judge and repair events contain each finding's confirmation basis,
source role and screenshot ID. Assert that the repair prompt says to inspect the
prior artifact against the confirmed evidence and forbids unrelated redesign.

- [ ] **Step 2: Verify RED**

```powershell
python -m pytest -q tests/builder_lab_cases/test_visual_repair_gate.py tests/builder_lab_cases/test_prompts.py
```

- [ ] **Step 3: Implement minimal propagation**

Pass confirmation metadata through committee result and forensic event payloads.
Do not weaken `artifact_fields` locks, browser gates, duplicate fingerprints or the
post-repair verifier.

- [ ] **Step 4: Verify GREEN**

Run the command from Step 2. Expected: all tests pass.

### Task 5: Frozen multi-widget benchmark

**Files:**
- Create: `scripts/replay_visual_committee_frozen.py`
- Create: `docs/release-evidence/2026-08-10-specialist-visual-critic-benchmark.md`
- Create: `output/visual-critic-specialist-benchmark-2026-08-10/manifest.json`

- [ ] **Step 1: Build a safe frozen-evidence manifest**

Reference only local/server forensic IDs and image hashes for Tutu, Mindbox and
T-Bank. Store no prompt text containing secrets, API keys, passwords or personal
data.

- [ ] **Step 2: Run each reasoning level on identical evidence**

For each dataset run Luna `medium`, `high` and `max`; repeat at least twice when the
bridge remains healthy. Store elapsed time, contract validity, per-role findings,
judge acceptance basis and usage counters returned by Codex.

- [ ] **Step 3: Score against the known-defect matrix**

Record true positives, false positives and missed visible defects. Do not call a
model better merely because it returned more findings.

- [ ] **Step 4: Select the production effort**

Choose High or Max only from measured recall, repeatability and latency. If Max does
not materially improve the stable findings, keep High.

### Task 6: Verification, deployment and editor journal

**Files:**
- Modify: `docs/product-journal/2026-08.md`
- Modify: `docs/telegram/content-backlog.md` only if the result has a distinct public story

- [ ] **Step 1: Run complete related verification**

```powershell
python -m pytest -q tests/builder_lab_cases/test_visual_critic.py tests/builder_lab_cases/test_visual_review.py tests/builder_lab_cases/test_visual_committee.py tests/builder_lab_cases/test_visual_repair_gate.py tests/builder_lab_cases/test_prompts.py tests/saas_cases/test_codex_bridge_provider.py tests/codex_bridge_cases/test_service.py tests/codex_bridge_cases/test_runner.py
python -m ruff check builder_lab/visual_critic.py builder_lab/visual_review.py builder_lab/visual_committee.py builder_lab/visual_gate.py builder_lab/prompts.py app/models/providers/codex_bridge.py tools/kaigo_codex_bridge/service.py tools/kaigo_codex_bridge/runner.py
git diff --check
```

- [ ] **Step 2: Deploy only verified files**

Build and restart the bridge/worker using the existing production runbook. Verify
bridge health, worker health and Studio HTTP 200 without printing secret values.

- [ ] **Step 3: Record the measured result in simple Russian**

Document the chosen reasoning effort, exact datasets, timings, stable defects,
false positives and remaining limitations. Clearly separate deployed facts from
future plans.
