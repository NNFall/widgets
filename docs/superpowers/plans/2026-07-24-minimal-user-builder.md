# Minimal User Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a minimal public flow where a user supplies a website URL plus an optional wish, watches automatic research/generation/review messages on the left, sees the live widget on the right, and can request one or more refinements.

**Architecture:** Extend the existing builder-lab instead of creating a second generator. A bounded reference pipeline captures one public HTTPS homepage at desktop/mobile, asks Gemini for a grounded visual brief, and injects that brief into the existing Direct generation request. Refinements create a new seeded run from the last accepted artifact, then reuse deterministic validation, BrowserAudit, Gemini visual critique, and the existing preview bridge.

**Tech Stack:** Python 3.11+, aiohttp, google-genai, Crawlee/Playwright, existing builder-lab HTML/CSS/JavaScript, unittest/pytest.

---

### Task 1: User request and grounded reference pipeline

**Files:**
- Create: `builder_lab/reference_pipeline.py`
- Modify: `builder_lab/models.py`
- Test: `tests/builder_lab_cases/test_reference_pipeline.py`
- Test: `tests/builder_lab_cases/test_models.py`

- [ ] **Step 1: Write failing request-model tests**

```python
request = BuilderRequest.from_dict({
    "engine": "direct",
    "brief": "Сделай консультанта",
    "source_url": "https://example.com/",
})
self.assertEqual(request.source_url, "https://example.com/")
self.assertEqual(request.to_dict()["source_url"], "https://example.com/")
```

- [ ] **Step 2: Run the focused tests and observe the missing field**

Run: `python -m pytest -q tests/builder_lab_cases/test_models.py`

- [ ] **Step 3: Add the bounded optional source URL**

```python
@dataclass(frozen=True)
class BuilderRequest:
    source_url: str = ""

    def __post_init__(self) -> None:
        source_url = validate_public_https_url(self.source_url) if self.source_url else ""
        object.__setattr__(self, "source_url", source_url)
```

- [ ] **Step 4: Write failing reference-pipeline tests**

```python
result = await pipeline.analyze("https://example.com/")
self.assertIn('"visual_summary"', result.context)
self.assertLessEqual(len(result.context), 8_000)
self.assertEqual(result.usage.prompt_tokens, 10)
```

- [ ] **Step 5: Implement the temporary evidence and visual-analysis adapter**

```python
@dataclass(frozen=True)
class ReferenceAnalysisResult:
    context: str
    summary: str
    usage: TokenUsage = TokenUsage()

class GeminiReferencePipeline:
    async def analyze(self, source_url: str) -> ReferenceAnalysisResult:
        crawl = await self._crawler.crawl(source_url)
        evidence = select_homepage_six_states(crawl)
        analysis = await self._analyzer(evidence)
        return compile_reference_context(analysis)
```

- [ ] **Step 6: Run focused tests**

Run: `python -m pytest -q tests/builder_lab_cases/test_models.py tests/builder_lab_cases/test_reference_pipeline.py`

### Task 2: Run research before the existing generator

**Files:**
- Modify: `builder_lab/store.py`
- Modify: `builder_lab/orchestrator.py`
- Modify: `scripts/run_builder_lab.py`
- Test: `tests/builder_lab_cases/test_store.py`
- Test: `tests/builder_lab_cases/test_orchestrator.py`

- [ ] **Step 1: Write failing store/orchestrator tests**

```python
snapshot = await orchestrator.start(BuilderRequest(
    engine=EngineName.DIRECT,
    brief="Сделай виджет",
    source_url="https://example.com/",
))
await orchestrator.wait(snapshot.run_id)
self.assertEqual(analyzer.urls, ["https://example.com/"])
self.assertIn("grounded context", engine.requests[0].reference_context)
```

- [ ] **Step 2: Run tests and observe missing analysis events**

Run: `python -m pytest -q tests/builder_lab_cases/test_store.py tests/builder_lab_cases/test_orchestrator.py`

- [ ] **Step 3: Add request replacement and reference events**

```python
await store.append_event(
    run_id,
    event_type="reference.started",
    stage=None,
    status="running",
    message="Анализируем сайт и делаем визуальные снимки",
)
analysis = await reference_analyzer(request.source_url)
request = replace(request, reference_context=analysis.context)
await store.update_request(run_id, request)
```

- [ ] **Step 4: Wire the bounded crawler and Gemini analyzer from configuration**

```python
reference_pipeline = GeminiReferencePipeline.from_config(config)
BuilderOrchestrator(..., reference_analyzer=reference_pipeline.analyze)
```

- [ ] **Step 5: Run focused tests**

Run: `python -m pytest -q tests/builder_lab_cases/test_store.py tests/builder_lab_cases/test_orchestrator.py tests/builder_lab_cases/test_config.py`

### Task 3: Refinement as a seeded, reviewed run

**Files:**
- Modify: `builder_lab/store.py`
- Modify: `builder_lab/orchestrator.py`
- Modify: `builder_lab/visual_gate.py`
- Modify: `builder_lab/web.py`
- Test: `tests/builder_lab_cases/test_store.py`
- Test: `tests/builder_lab_cases/test_orchestrator.py`
- Test: `tests/builder_lab_cases/test_visual_repair_gate.py`
- Test: `tests/builder_lab_cases/test_web.py`

- [ ] **Step 1: Write failing seeded-run and endpoint tests**

```python
response = await client.post(
    f"/api/runs/{run_id}/refine",
    json={"message": "Сделай шапку спокойнее"},
)
self.assertEqual(response.status, 202)
self.assertEqual(orchestrator.refinements[-1], (run_id, "Сделай шапку спокойнее"))
```

- [ ] **Step 2: Run tests and observe the missing route**

Run: `python -m pytest -q tests/builder_lab_cases/test_store.py tests/builder_lab_cases/test_orchestrator.py tests/builder_lab_cases/test_visual_repair_gate.py tests/builder_lab_cases/test_web.py`

- [ ] **Step 3: Generalize the visual gate to consecutive motion-polish revisions**

```python
if candidate.stage is not Stage.MOTION_POLISH:
    raise ValueError("visual gate accepts only motion_polish artifacts")
if candidate.revision != previous.revision + 1:
    raise ValueError("visual gate requires consecutive revisions")
```

- [ ] **Step 4: Seed a new run and generate one reviewed revision**

```python
async def refine(self, source_run_id: str, message: str) -> BuilderRunSnapshot:
    source = await self.store.snapshot(source_run_id)
    previous = await self.store.artifact(source_run_id)
    request = refinement_request(source.request, message)
    snapshot = await self.store.create_seeded(request, previous)
    self._spawn_refinement(snapshot.run_id, request, previous)
    return snapshot
```

- [ ] **Step 5: Add the bounded refine endpoint**

```python
async def refine_run(request: web.Request) -> web.Response:
    payload = await request.json()
    message = bounded_refinement_message(payload.get("message"))
    snapshot = await request.app[ORCHESTRATOR_KEY].refine(
        request.match_info["run_id"], message
    )
    return web.json_response(snapshot.to_dict(), status=202)
```

- [ ] **Step 6: Run focused tests**

Run: `python -m pytest -q tests/builder_lab_cases/test_store.py tests/builder_lab_cases/test_orchestrator.py tests/builder_lab_cases/test_visual_repair_gate.py tests/builder_lab_cases/test_web.py`

### Task 4: Minimal two-column user interface

**Files:**
- Modify: `builder_lab/ui.py`
- Test: `tests/builder_lab_cases/test_web.py`
- Test: `tests/builder_lab_cases/test_demo_browser.py`

- [ ] **Step 1: Write failing HTML contract tests**

```python
self.assertIn('id="source-url"', body)
self.assertIn('id="brief"', body)
self.assertIn('id="builder-messages"', body)
self.assertIn('id="refinement"', body)
self.assertIn("source_url:elements['source-url'].value", body)
self.assertIn("api/runs/${currentRun}/refine", body)
```

- [ ] **Step 2: Run the tests and observe missing controls**

Run: `python -m pytest -q tests/builder_lab_cases/test_web.py`

- [ ] **Step 3: Render the minimal form, dialogue, preview, and refinement composer**

```html
<input id="source-url" type="url" placeholder="https://example.com" required>
<textarea id="brief" placeholder="Что должен делать AI-сотрудник?"></textarea>
<div id="builder-messages" aria-live="polite"></div>
<textarea id="refinement" placeholder="Что изменить в готовом виджете?"></textarea>
```

- [ ] **Step 4: Convert streamed events into readable assistant messages**

```javascript
if (event.type === 'reference.started') addBuilderMessage('assistant', event.message);
if (event.type === 'artifact.committed') addBuilderMessage('assistant', event.message);
if (event.type === 'run.completed') enableRefinement(true);
```

- [ ] **Step 5: Run UI and bridge tests**

Run: `python -m pytest -q tests/builder_lab_cases/test_web.py tests/builder_lab_cases/test_demo_browser.py`

### Task 5: Documentation, deployment, and real browser proof

**Files:**
- Modify: `README.md`
- Modify: `docs/KAIGO_BUILDER_LAB_OPERATIONS.md`
- Modify: `docker-compose.yml`
- Modify: `.env.example`

- [ ] **Step 1: Document the exact MVP flow and operational limits**

```text
URL + optional wish -> bounded homepage capture -> Gemini grounded visual brief
-> five Direct stages -> deterministic validation -> BrowserAudit -> Gemini critic
-> live preview -> seeded refinement.
```

- [ ] **Step 2: Run focused and full verification**

Run: `python -m pytest -q tests/builder_lab_cases/test_models.py tests/builder_lab_cases/test_reference_pipeline.py tests/builder_lab_cases/test_store.py tests/builder_lab_cases/test_orchestrator.py tests/builder_lab_cases/test_visual_repair_gate.py tests/builder_lab_cases/test_web.py tests/builder_lab_cases/test_demo_browser.py`

Run: `python -m pytest -q`

Run: `python -m compileall -q builder_lab scripts tests`

Run: `python -m ruff check builder_lab scripts tests`

- [ ] **Step 3: Deploy with the existing deterministic deployment script**

Run: `bash scripts/deploy_builder_lab.sh`

- [ ] **Step 4: Verify in the explicitly requested in-app browser**

Open `https://kaigo.space/builder-demo/`, submit a different public HTTPS site plus a wish, observe research/generation/review messages, inspect desktop and mobile previews, send a widget chat question, submit a refinement, and confirm the next accepted revision appears without reloading the builder page.

- [ ] **Step 5: Commit and push the verified branch**

```bash
git add builder_lab scripts tests docs README.md docker-compose.yml .env.example
git commit -m "feat: add minimal user widget builder"
git push
```
