# Gemini 3.6 and Antigravity Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate Kaigo Builder Lab to the approved Gemini model matrix, allow unrestricted generated JavaScript and CSS motion in the isolated preview, and publish a reproducible RAW BUREAU baseline/direct/Antigravity comparison.

**Architecture:** Keep the existing `WidgetArtifact` + SSE revision pipeline as the common boundary for both engines. Extend the artifact with JavaScript, stage summary, and declared layout; run generated JavaScript only in the already sandboxed preview iframe. Freeze one evidence bundle, run Direct and Antigravity against it, then build a static comparison package from versioned outputs and verify the deployed pages with the in-app Browser.

**Tech Stack:** Python 3.12, `google-genai`, `aiohttp`, Playwright/Chromium, dataclasses, JSON Schema structured output, pytest/unittest, Docker Compose, nginx/systemd deployment.

---

## File map

- `builder_lab/model_config.py`: model-aware thinking and sampling configuration.
- `builder_lab/config.py`: environment-backed model/thinking/token settings.
- `builder_lab/models.py`: shared artifact fields.
- `builder_lab/prompts.py`: structured schema and generation instructions.
- `builder_lab/validation.py`: transport/structure validation without creative JS/motion bans.
- `builder_lab/preview.py`: isolated execution of generated JavaScript.
- `builder_lab/engines/gemini_direct.py`: Gemini 3.6 structured calls.
- `builder_lab/chat.py`: Gemini 3.5 Flash-Lite medium chat.
- `builder_lab/visual_critic.py`: Gemini 3.5 Flash high visual review.
- `scripts/analyze_reference_site.py`: Gemini 3.5 Flash high source analysis.
- `builder_lab/engines/antigravity.py`: matching artifact contract and agent budget.
- `builder_lab/orchestrator.py`, `builder_lab/store.py`, `builder_lab/ui.py`: stage summaries, diffs, SSE and preview revisions.
- `builder_lab/reference_crawler.py`: adaptive settling and wide desktop evidence.
- `builder_lab/browser_audit.py`, `builder_lab/visual_models.py`: flexible geometry, JavaScript telemetry and expanded screenshots.
- `scripts/build_raw_bureau_comparison.py`: immutable input bundle, archive, reports and comparison pages.
- `deploy/comparison/`: immutable generated deployment artifacts, excluded from secrets.
- `tests/builder_lab_cases/`: unit, integration and real-browser regression coverage.
- `.env.example`, `docker-compose.yml`, `README.md`: deployment configuration and operator commands.

Intentionally deferred by the approved design: the automatic prompt compiler,
independent strict reviewer, and productized 5/10/20-minute modes. This plan
records the evidence and interfaces those later features will consume but does
not change the frozen chat prompt during the comparison.

### Task 1: Model-aware Gemini configuration

**Files:**
- Create: `builder_lab/model_config.py`
- Modify: `builder_lab/config.py`
- Modify: `builder_lab/engines/gemini_direct.py`
- Modify: `builder_lab/chat.py`
- Modify: `builder_lab/visual_critic.py`
- Modify: `scripts/analyze_reference_site.py`
- Modify: `scripts/run_builder_lab.py`
- Test: `tests/builder_lab_cases/test_model_config.py`
- Test: `tests/builder_lab_cases/test_config.py`
- Test: `tests/builder_lab_cases/test_gemini_direct.py`
- Test: `tests/builder_lab_cases/test_chat.py`
- Test: `tests/builder_lab_cases/test_visual_critic.py`
- Test: `tests/builder_lab_cases/test_analyze_reference_site.py`

- [ ] **Step 1: Write failing model-policy tests**

```python
def test_gemini_36_high_omits_sampling():
    policy = generation_policy("gemini-3.6-flash", "high")
    assert policy.thinking_config.thinking_level.value == "HIGH"
    assert policy.sampling_kwargs == {}


def test_gemini_35_flash_high_keeps_supported_sampling():
    policy = generation_policy("gemini-3.5-flash", "high", temperature=0.4)
    assert policy.thinking_config.thinking_level.value == "HIGH"
    assert policy.sampling_kwargs == {"temperature": 0.4, "top_p": 1.0}


def test_flash_lite_medium_omits_sampling():
    policy = generation_policy("gemini-3.5-flash-lite", "medium")
    assert policy.thinking_config.thinking_level.value == "MEDIUM"
    assert policy.sampling_kwargs == {}
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```powershell
python -m unittest tests.builder_lab_cases.test_model_config -v
```

Expected: import failure for `builder_lab.model_config`.

- [ ] **Step 3: Implement the shared policy**

```python
@dataclass(frozen=True)
class GenerationPolicy:
    thinking_config: types.ThinkingConfig | None
    sampling_kwargs: dict[str, float]


def generation_policy(model: str, level: str, *, temperature: float | None = None) -> GenerationPolicy:
    normalized = model.lower().removeprefix("models/")
    thinking = types.ThinkingConfig(
        thinking_level=getattr(types.ThinkingLevel, level.strip().upper())
    )
    sampling = {}
    if not (
        normalized.startswith("gemini-3.6-")
        or normalized.startswith("gemini-3.5-flash-lite")
    ):
        if temperature is not None:
            sampling = {"temperature": temperature, "top_p": 1.0}
    return GenerationPolicy(thinking_config=thinking, sampling_kwargs=sampling)
```

Add config fields:

```python
builder_thinking_level: str
chat_thinking_level: str
visual_critic_thinking_level: str
reference_analyzer_model: str
reference_analyzer_thinking_level: str
antigravity_max_total_tokens: int
```

Defaults:

```text
GEMINI_BUILDER_MODEL=gemini-3.6-flash
GEMINI_BUILDER_THINKING_LEVEL=high
GEMINI_VISUAL_CRITIC_MODEL=gemini-3.5-flash
GEMINI_VISUAL_CRITIC_THINKING_LEVEL=high
GEMINI_REFERENCE_ANALYZER_MODEL=gemini-3.5-flash
GEMINI_REFERENCE_ANALYZER_THINKING_LEVEL=high
GEMINI_CHAT_MODEL=gemini-3.5-flash-lite
GEMINI_CHAT_THINKING_LEVEL=medium
GEMINI_ANTIGRAVITY_MAX_TOTAL_TOKENS=500000
```

- [ ] **Step 4: Wire each caller to its own policy**

Build `GenerateContentConfig` with:

```python
policy = generation_policy(self.model, self.thinking_level, temperature=temperature)
config = types.GenerateContentConfig(
    **policy.sampling_kwargs,
    max_output_tokens=max_output_tokens,
    response_mime_type="application/json",
    response_json_schema=schema,
    thinking_config=policy.thinking_config,
)
```

Pass the configured thinking level from `scripts/run_builder_lab.py` into Direct,
chat, and critic; use the analyzer-specific environment variables in
`scripts/analyze_reference_site.py`.

- [ ] **Step 5: Run model/config tests**

Run:

```powershell
python -m unittest `
  tests.builder_lab_cases.test_model_config `
  tests.builder_lab_cases.test_config `
  tests.builder_lab_cases.test_gemini_direct `
  tests.builder_lab_cases.test_chat `
  tests.builder_lab_cases.test_visual_critic `
  tests.builder_lab_cases.test_analyze_reference_site -v
```

Expected: all tests pass and no Gemini 3.6/3.5-Lite config contains sampling fields.

- [ ] **Step 6: Commit**

```powershell
git add builder_lab/model_config.py builder_lab/config.py builder_lab/engines/gemini_direct.py builder_lab/chat.py builder_lab/visual_critic.py scripts/analyze_reference_site.py scripts/run_builder_lab.py tests/builder_lab_cases
git commit -m "feat: migrate builder workloads to latest Gemini models"
```

### Task 2: Extend the common artifact contract

**Files:**
- Modify: `builder_lab/models.py`
- Modify: `builder_lab/prompts.py`
- Modify: `builder_lab/snapshots.py`
- Modify: `builder_lab/validation.py`
- Modify: `builder_lab/engines/antigravity.py`
- Test: `tests/builder_lab_cases/test_models.py`
- Test: `tests/builder_lab_cases/test_validation.py`
- Test: `tests/builder_lab_cases/test_snapshots.py`
- Test: `tests/builder_lab_cases/test_antigravity.py`

- [ ] **Step 1: Write failing round-trip and validation tests**

```python
def test_artifact_round_trips_experimental_fields():
    candidate = artifact(
        change_summary="Добавлено появление по прокрутке.",
        javascript="addEventListener('scroll', () => document.body.dataset.y = scrollY)",
        layout_contract={"desktop_panel_width": "428px"},
    )
    assert WidgetArtifact.from_dict(candidate.to_dict()) == candidate


def test_free_javascript_and_infinite_animation_are_allowed():
    candidate = artifact(
        javascript="setInterval(() => document.body.classList.toggle('pulse'), 50)",
        css=".pulse{animation:spin 100s linear infinite}@keyframes spin{to{rotate:1turn}}",
    )
    codes = {issue.code for issue in validate_artifact(candidate)}
    assert "animation_iterations_exceeded" not in codes
    assert "too_many_animations" not in codes
    assert "generated_javascript" not in codes
```

- [ ] **Step 2: Verify focused failure**

Run:

```powershell
python -m unittest tests.builder_lab_cases.test_models tests.builder_lab_cases.test_validation -v
```

Expected: constructor/schema assertions fail for the new fields.

- [ ] **Step 3: Add backward-compatible fields**

```python
MAX_JAVASCRIPT_BYTES = 256 * 1024


@dataclass(frozen=True)
class WidgetArtifact:
    schema_version: str
    revision: int
    stage: Stage
    art_direction: str
    body_html: str
    css: str
    theme_tokens: dict[str, str] = field(default_factory=dict)
    suggested_actions: tuple[str, ...] = ()
    change_summary: str = ""
    javascript: str = ""
    layout_contract: dict[str, str] = field(default_factory=dict)
```

`from_dict()` accepts absent fields for baseline compatibility. New Gemini and
Antigravity schemas require all three fields. Validate only byte size, text
shape, serializability and existing required chat regions; remove animation
count/duration/iteration rejection and generated-JavaScript rejection.

- [ ] **Step 4: Update Antigravity snapshot validator**

Require the new keys, enforce only byte/JSON/required-region integrity, and
remove checks rejecting `infinite`, URLs, inline handlers and JavaScript.

- [ ] **Step 5: Run contract tests**

Run:

```powershell
python -m unittest `
  tests.builder_lab_cases.test_models `
  tests.builder_lab_cases.test_validation `
  tests.builder_lab_cases.test_snapshots `
  tests.builder_lab_cases.test_antigravity -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add builder_lab/models.py builder_lab/prompts.py builder_lab/snapshots.py builder_lab/validation.py builder_lab/engines/antigravity.py tests/builder_lab_cases
git commit -m "feat: add experimental JavaScript artifact contract"
```

### Task 3: Execute generated JavaScript in the isolated preview

**Files:**
- Modify: `builder_lab/preview.py`
- Modify: `builder_lab/browser_audit.py`
- Test: `tests/builder_lab_cases/test_preview.py`
- Test: `tests/builder_lab_cases/test_preview_browser.py`
- Test: `tests/builder_lab_cases/test_browser_audit.py`

- [ ] **Step 1: Write failing browser tests**

```python
async def test_generated_javascript_runs_inside_preview(self):
    candidate = artifact(
        javascript="document.querySelector('[data-region=root]').dataset.generated='yes'"
    )
    await self.mount(candidate)
    assert await self.frame.locator("[data-region=root]").get_attribute("data-generated") == "yes"


async def test_generated_javascript_cannot_reach_parent_document(self):
    candidate = artifact(
        javascript="try{parent.document.body.dataset.pwned='1'}catch(e){document.body.dataset.isolated='1'}"
    )
    await self.mount(candidate)
    assert await self.page.locator("body").get_attribute("data-pwned") is None
    assert await self.frame.locator("body").get_attribute("data-isolated") == "1"
```

- [ ] **Step 2: Verify failure**

Run:

```powershell
python -m unittest tests.builder_lab_cases.test_preview tests.builder_lab_cases.test_preview_browser -v
```

Expected: generated script marker is absent.

- [ ] **Step 3: Inject generated code after the trusted runtime**

```python
def _safe_script(script: str) -> str:
    return re.sub(r"</\s*script", "<\\\\/script", script, flags=re.IGNORECASE)
```

Append:

```html
<script data-kaigo-generated>
try {
  /* artifact.javascript */
} catch (error) {
  console.error("kaigo-generated-javascript", error);
}
</script>
```

Keep iframe `sandbox="allow-scripts"` without `allow-same-origin`; never inject
API keys or server configuration into the generated document.

- [ ] **Step 4: Record generated-code telemetry**

The browser audit collects console messages, page errors, unhandled rejections,
network requests and pending async resources. Generated-code findings are
reported separately from trusted-runtime contract failures.

- [ ] **Step 5: Run preview and browser tests**

Run:

```powershell
python -m unittest `
  tests.builder_lab_cases.test_preview `
  tests.builder_lab_cases.test_preview_browser `
  tests.builder_lab_cases.test_browser_audit -v
```

Expected: generated JS executes, parent remains isolated, existing chat tests pass.

- [ ] **Step 6: Commit**

```powershell
git add builder_lab/preview.py builder_lab/browser_audit.py tests/builder_lab_cases
git commit -m "feat: run generated JavaScript in sandboxed previews"
```

### Task 4: Replace fixed geometry and prompt restrictions

**Files:**
- Modify: `builder_lab/prompts.py`
- Modify: `builder_lab/browser_audit.py`
- Modify: `builder_lab/engines/antigravity.py`
- Test: `tests/builder_lab_cases/test_gemini_direct.py`
- Test: `tests/builder_lab_cases/test_browser_audit.py`
- Test: `tests/builder_lab_cases/test_antigravity.py`

- [ ] **Step 1: Write failing prompt and geometry tests**

```python
def test_prompt_requests_free_javascript_and_declared_layout():
    prompt = build_stage_prompt(
        request=BuilderRequest(engine=EngineName.DIRECT, brief="Создай виджет"),
        stage=Stage.ART_DIRECTION,
        revision=1,
    )
    assert "javascript" in prompt
    assert "layout_contract" in prompt
    assert "любой JavaScript" in prompt
    assert "ровно 372" not in prompt
    assert "216×46" not in prompt
    assert "infinite" not in prompt.lower() or "разреш" in prompt.lower()
```

Browser cases must pass with desktop panels at 336px and 432px and fail when
close/send are outside the viewport or the root creates horizontal overflow.

- [ ] **Step 2: Verify focused failure**

Run:

```powershell
python -m unittest tests.builder_lab_cases.test_gemini_direct tests.builder_lab_cases.test_browser_audit -v
```

Expected: current fixed 372px/216px assertions fail.

- [ ] **Step 3: Rewrite prompt bounds**

Require a compact host-subordinate widget, required chat regions, real actions,
`change_summary`, free JavaScript, unrestricted CSS motion, declared layout and
mobile support. Remove fixed dimensions, finite-motion instructions, generated
JS prohibition and the three-action total.

- [ ] **Step 4: Replace exact release-gate geometry**

Keep:

```python
MIN_INTERACTIVE_TARGET_PX = 43.5
MAX_HORIZONTAL_OVERFLOW_PX = 1.0
```

Validate controls in viewport, close/send reachability, initial transcript fit,
mobile safe margins and total visible area. Treat declared desktop
`320..440px` as a recommendation warning, not a blocker when the brief and
actual viewport remain usable.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
python -m unittest `
  tests.builder_lab_cases.test_gemini_direct `
  tests.builder_lab_cases.test_browser_audit `
  tests.builder_lab_cases.test_antigravity -v
```

Expected: flexible valid geometries pass and broken interaction still fails.

- [ ] **Step 6: Commit**

```powershell
git add builder_lab/prompts.py builder_lab/browser_audit.py builder_lab/engines/antigravity.py tests/builder_lab_cases
git commit -m "feat: let Gemini choose widget geometry and motion"
```

### Task 5: Stage summaries and observable revision diffs

**Files:**
- Modify: `builder_lab/models.py`
- Modify: `builder_lab/store.py`
- Modify: `builder_lab/orchestrator.py`
- Modify: `builder_lab/ui.py`
- Test: `tests/builder_lab_cases/test_store.py`
- Test: `tests/builder_lab_cases/test_orchestrator.py`
- Test: `tests/builder_lab_cases/test_web.py`

- [ ] **Step 1: Write failing event tests**

```python
committed = [event for event in events if event.event_type == "artifact.committed"]
assert committed[0].message == first.change_summary
assert committed[0].changes == ("body_html", "css", "javascript", "layout_contract")
```

- [ ] **Step 2: Verify failure**

Run:

```powershell
python -m unittest tests.builder_lab_cases.test_store tests.builder_lab_cases.test_orchestrator -v
```

Expected: `changes` is not present and commit message is generic.

- [ ] **Step 3: Add deterministic diff metadata**

```python
def artifact_changed_fields(before: WidgetArtifact | None, after: WidgetArtifact) -> tuple[str, ...]:
    fields = ("art_direction", "body_html", "css", "javascript", "layout_contract", "theme_tokens")
    return tuple(name for name in fields if before is None or getattr(before, name) != getattr(after, name))
```

Add `changes` to `BuilderEvent`; set `artifact.committed.message` to the bounded
`change_summary` and attach computed changes.

- [ ] **Step 4: Render summaries in the UI**

Show the human summary as the main event text and a separate compact line such
as `Подтверждено: HTML · CSS · JavaScript · layout`. Continue loading the new
revision immediately when the event arrives.

- [ ] **Step 5: Run store/orchestrator/web tests**

Run:

```powershell
python -m unittest `
  tests.builder_lab_cases.test_store `
  tests.builder_lab_cases.test_orchestrator `
  tests.builder_lab_cases.test_web -v
```

Expected: all five Direct revisions emit summaries and computed diffs.

- [ ] **Step 6: Commit**

```powershell
git add builder_lab/models.py builder_lab/store.py builder_lab/orchestrator.py builder_lab/ui.py tests/builder_lab_cases
git commit -m "feat: expose revision summaries and artifact diffs"
```

### Task 6: Adaptive source capture and expanded visual evidence

**Files:**
- Modify: `builder_lab/reference_crawler.py`
- Modify: `builder_lab/reference_models.py`
- Modify: `builder_lab/browser_audit.py`
- Modify: `builder_lab/visual_models.py`
- Modify: `builder_lab/visual_critic.py`
- Test: `tests/builder_lab_cases/test_reference_crawler.py`
- Test: `tests/builder_lab_cases/test_reference_models.py`
- Test: `tests/builder_lab_cases/test_browser_audit.py`
- Test: `tests/builder_lab_cases/test_visual_critic.py`

- [ ] **Step 1: Write failing stability and evidence tests**

Use a local page that reveals content after scroll with delayed DOM mutations.
Assert the crawler waits until the delayed block is visible and captures
`desktop_wide` at 1920×1080. Assert the audit returns full-context and cropped
screenshots with unique IDs.

- [ ] **Step 2: Verify failure**

Run:

```powershell
python -m unittest `
  tests.builder_lab_cases.test_reference_crawler `
  tests.builder_lab_cases.test_browser_audit `
  tests.builder_lab_cases.test_visual_critic -v
```

Expected: no `desktop_wide` evidence and delayed block missing.

- [ ] **Step 3: Implement adaptive settle**

Add a Playwright helper that waits for:

```javascript
{
  documentReady: document.readyState === "complete",
  pendingImages: [...document.images].filter(img => !img.complete).length,
  fontReady: document.fonts?.status === "loaded",
  mutationQuietMs,
  stableLayoutSamples
}
```

Use minimum settle + quiet-window + maximum deadline instead of a single sleep.
Capture 1920×1080 in addition to 1440×900 and 390×844.

- [ ] **Step 4: Add contextual and close-up audit frames**

Produce:

```text
wide.closed
wide.open_initial
wide.after_turn_2
wide.launcher_crop
wide.panel_crop
wide.detail_crop
mobile.closed
mobile.open_initial
mobile.after_turn_2
```

Capture active-motion evidence first, then settle animations for deterministic
layout measurements. Pass full frames and crops to Gemini visual critic.

- [ ] **Step 5: Run crawler/audit/critic tests**

Run:

```powershell
python -m unittest `
  tests.builder_lab_cases.test_reference_crawler `
  tests.builder_lab_cases.test_reference_models `
  tests.builder_lab_cases.test_browser_audit `
  tests.builder_lab_cases.test_visual_critic -v
```

Expected: adaptive content and all evidence types pass validation.

- [ ] **Step 6: Commit**

```powershell
git add builder_lab/reference_crawler.py builder_lab/reference_models.py builder_lab/browser_audit.py builder_lab/visual_models.py builder_lab/visual_critic.py tests/builder_lab_cases
git commit -m "feat: expand adaptive visual evidence capture"
```

### Task 7: Immutable experiment bundle and comparison publisher

**Files:**
- Create: `scripts/build_raw_bureau_comparison.py`
- Create: `builder_lab/comparison.py`
- Modify: `.gitignore`
- Modify: `README.md`
- Test: `tests/builder_lab_cases/test_comparison.py`

- [ ] **Step 1: Write failing bundle/report tests**

```python
bundle = freeze_bundle(source_dir, output_dir)
assert bundle.manifest["schema_version"] == 1
assert verify_bundle(output_dir)
assert bundle.manifest["files"]["brief.txt"]["sha256"]

page = render_comparison_page(baseline, direct, antigravity)
assert "RAW BUREAU — baseline" in page
assert "Gemini 3.6 Flash / high" in page
assert "Antigravity" in page
```

- [ ] **Step 2: Verify failure**

Run:

```powershell
python -m unittest tests.builder_lab_cases.test_comparison -v
```

Expected: modules do not exist.

- [ ] **Step 3: Implement immutable bundles**

The manifest contains relative path, SHA-256, byte count, creation timestamp,
source URL, brief digest, model matrix and acceptance-profile ID. Refuse to
overwrite an existing bundle whose digest differs.

- [ ] **Step 4: Implement versioned outputs**

Write:

```text
comparison/
  manifest.json
  index.html
  archive/raw-bureau-v1/{index.html,artifact.json,report.json,evidence/}
  direct-3-6/{index.html,artifact.json,report.json,revisions/,evidence/}
  antigravity-3-6/{index.html,artifact.json,report.json,evidence/}
```

Every report includes exact model, thinking, request/response IDs where
available, usage, elapsed time, validation, browser findings and known errors.

- [ ] **Step 5: Run comparison tests**

Run:

```powershell
python -m unittest tests.builder_lab_cases.test_comparison -v
```

Expected: digest tampering fails and all comparison links resolve locally.

- [ ] **Step 6: Commit**

```powershell
git add builder_lab/comparison.py scripts/build_raw_bureau_comparison.py tests/builder_lab_cases/test_comparison.py .gitignore README.md
git commit -m "feat: build reproducible widget comparison reports"
```

### Task 8: Full local verification

**Files:**
- Modify if required by failures: files changed in Tasks 1–7

- [ ] **Step 1: Run formatting/static compilation**

Run:

```powershell
python -m compileall builder_lab scripts tests
git diff --check
```

Expected: exit code 0.

- [ ] **Step 2: Run the complete Builder Lab suite**

Run:

```powershell
python -m unittest discover -s tests/builder_lab_cases -p "test_*.py" -v
```

Expected: all tests pass.

- [ ] **Step 3: Run local real-browser smoke**

Run the Builder Lab locally and execute:

```powershell
python scripts/smoke_builder_lab.py --base-url http://127.0.0.1:8091/ --engine direct --require-visual-audit
```

Expected: five committed Direct revisions, required screenshots, two chat turns,
zero runtime-contract errors.

- [ ] **Step 4: Commit only test-derived fixes**

```powershell
git add builder_lab scripts tests
git commit -m "test: harden Gemini comparison pipeline"
```

### Task 9: Real Direct and Antigravity runs

**Files:**
- Generate outside Git: `outputs/kaigo-comparison-2026-07-23/**`

- [ ] **Step 1: Freeze the RAW BUREAU bundle**

Run:

```powershell
python scripts/build_raw_bureau_comparison.py freeze `
  --source-url https://rawbureau.ru/ `
  --existing-baseline "D:\papka for all\work\kaigo.widgets\outputs\kaigo-audit-2026-07-23" `
  --output "D:\papka for all\work\kaigo.widgets\outputs\kaigo-comparison-2026-07-23"
```

Expected: a manifest with verified SHA-256 values and archived baseline.

- [ ] **Step 2: Run Direct**

Use the frozen bundle with `gemini-3.6-flash/high`. Preserve every revision,
event, response metadata, screenshot and audit report.

Expected: completed revision 5 plus visual gate result.

- [ ] **Step 3: Run Antigravity**

Use the same bundle with `antigravity-preview-05-2026`, 500k token budget and a
20-minute user-facing profile.

Expected: completed `agent_build` artifact or a transparent `incomplete` report
that can be continued in the same environment without altering the input bundle.

- [ ] **Step 4: Build local comparison**

Run:

```powershell
python scripts/build_raw_bureau_comparison.py render `
  --output "D:\papka for all\work\kaigo.widgets\outputs\kaigo-comparison-2026-07-23"
```

Expected: all four public-relative routes resolve and report actual model/usage.

### Task 10: Deploy, verify in the in-app Browser, and publish Git

**Files:**
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `README.md`
- Server-only: `/root/ai_project/docker-compose.yml`
- Server-only: `/root/ai_project/data/builder-demo/`
- Server-only: `/etc/nginx/sites-available/kaigo.space`

- [ ] **Step 1: Update deployment defaults**

Set the approved model/thinking matrix and Antigravity budget without placing
the API key in Git.

- [ ] **Step 2: Deploy code and comparison bundle**

Use the existing server deployment script and nginx layout. Preserve the
existing public demo until the new service passes health and smoke checks.

- [ ] **Step 3: Verify public HTTP and assets**

Check:

```text
https://kaigo.space/builder-demo/
https://kaigo.space/builder-demo/compare/
https://kaigo.space/builder-demo/archive/raw-bureau-v1/
https://kaigo.space/builder-demo/direct-3-6/
https://kaigo.space/builder-demo/antigravity-3-6/
```

Expected: HTTP 200, no mixed-content failures, no missing screenshot assets.

- [ ] **Step 4: Use the in-app Browser skill**

Open the comparison page in the in-app Browser. For Direct and Antigravity:

1. inspect the full 1920×1080 host-context frame;
2. open the widget;
3. send two related questions;
4. confirm two real model responses and preserved history;
5. close/reopen;
6. switch mobile viewport and repeat open/send/close;
7. inspect console errors and screenshot evidence.

Expected: both variants are interactive and the comparison metadata matches the
stored reports.

- [ ] **Step 5: Run final verification before completion**

Run local full tests again, public smoke against `kaigo.space`, `git diff
--check`, `git status`, and compare deployed commit SHA with local HEAD.

- [ ] **Step 6: Commit and push**

```powershell
git add .env.example docker-compose.yml README.md
git commit -m "deploy: publish Gemini and Antigravity comparison"
git push origin codex/gemini-technical-foundation
```

Expected: GitHub branch contains the exact deployed source commit and the public
comparison references the same commit SHA.
