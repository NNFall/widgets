# Two-pass Reference Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Warm long animated pages with a real wheel traversal before returning to the top and collecting viewport screenshots in a second traversal.

**Architecture:** Keep the existing scroll-state detection and wheel-first fallback, but separate traversal from evidence capture. Scope image readiness to the current viewport, add a bounded downward warm-up and verified reverse traversal, then reuse the existing capture loop from the restored initial state.

**Tech Stack:** Python 3.11, asyncio, Playwright, unittest/pytest, Ruff.

---

### Task 1: Reproduce the Flowwow lazy-image deadlock

**Files:**
- Modify: `tests/builder_lab_cases/test_reference_crawler.py`

- [ ] **Step 1: Add a deterministic long-page fixture**

Add a `/lazy-prewarm` response to `LazyFixtureHandler` with an off-screen
`loading="lazy"` image whose `src` is already present and a wheel counter:

```python
elif path == "/lazy-prewarm":
    body = b"""<!doctype html><meta charset='utf-8'>
    <style>body{margin:0}.spacer{height:7200px}img{display:block;width:120px;height:120px}.tail{height:900px}</style>
    <h1>Lazy prewarm fixture</h1><div class='spacer'></div>
    <img id='late' loading='lazy' src='/lazy.png' alt='late'><div class='tail'></div>
    <script>addEventListener('wheel',()=>document.body.dataset.wheels=String(1 + +(document.body.dataset.wheels || 0)),{passive:true})</script>"""
    content_type = "text/html; charset=utf-8"
```

- [ ] **Step 2: Add the two-pass behavioral test**

Patch `_take_screenshot` to record `scrollY`, the wheel count, and the lazy
image state for the first `top` capture. Assert that wheel events already
occurred, the viewport was restored to `scrollY == 0`, the image has natural
dimensions, and the evidence reports the two-pass reset strategy.

```python
def test_two_pass_warmup_loads_offscreen_lazy_image_before_top_capture(self):
    first_top = {}
    original = reference_crawler_module._take_screenshot

    async def record(page, **kwargs):
        if kwargs["position"] == "top" and not first_top:
            first_top.update(await page.evaluate(
                "() => ({y:scrollY,wheels:+(document.body.dataset.wheels||0),"
                "complete:late.complete,naturalWidth:late.naturalWidth})"
            ))
        return await original(page, **kwargs)

    # Capture /lazy-prewarm with page_timeout_seconds=5 and enough scroll steps.
    self.assertEqual(first_top["y"], 0)
    self.assertGreater(first_top["wheels"], 0)
    self.assertTrue(first_top["complete"])
    self.assertGreater(first_top["naturalWidth"], 0)
    self.assertEqual(evidence.reset_strategy, "wheel-prewarm-return-top")
```

- [ ] **Step 3: Run the regression test and verify RED**

Run:

```powershell
python -m pytest tests/builder_lab_cases/test_reference_crawler.py::ReferenceCaptureTests::test_two_pass_warmup_loads_offscreen_lazy_image_before_top_capture -q
```

Expected: failure from the current global image wait before any wheel event.

- [ ] **Step 4: Commit the failing regression**

```powershell
git add tests/builder_lab_cases/test_reference_crawler.py
git commit -m "Test two-pass lazy reference warmup"
```

### Task 2: Scope readiness to the current viewport

**Files:**
- Modify: `builder_lab/reference_crawler.py`
- Test: `tests/builder_lab_cases/test_reference_crawler.py`

- [ ] **Step 1: Replace global image readiness with viewport readiness**

Rename `_wait_for_fonts_and_visible_images` to
`_wait_for_fonts_and_viewport_images`. Its predicate must include only images
whose rectangles intersect the viewport plus a `200px` margin:

```javascript
const intersects = r.bottom >= -200 && r.top <= innerHeight + 200
  && r.right >= -200 && r.left <= innerWidth + 200;
```

An image is ready when it has no source, has loaded natural dimensions, or has
completed with no current source. Off-screen images are excluded.

- [ ] **Step 2: Apply the same scope to visual-quiet readiness**

Inside `_wait_for_visual_quiet`, calculate `pendingImages` and `ready` from the
same viewport-intersection rule instead of all `document.images`. Keep the
existing maximum wait as a non-throwing bound.

- [ ] **Step 3: Run focused readiness tests**

Run:

```powershell
python -m pytest tests/builder_lab_cases/test_reference_crawler.py -q -k "lazy or warmup or visual_quiet"
```

Expected: the new regression advances past the initial readiness stage; any
remaining failure must concern the not-yet-implemented prewarm/reset behavior.

### Task 3: Add bounded warm-up and verified return

**Files:**
- Modify: `builder_lab/reference_crawler.py`
- Test: `tests/builder_lab_cases/test_reference_crawler.py`

- [ ] **Step 1: Extract a bounded downward warm-up helper**

Add `_warm_reference_page(page, settings)` that:

1. records the initial `_SCROLL_STATE_SCRIPT` result;
2. calls `_scroll_once` with a positive `0.7 * viewport height` step;
3. waits `scroll_delay_ms`, visual quiet, and viewport images at each position;
4. uses existing stable-end, virtual-end, step, and height bounds;
5. returns the initial state and non-fatal warm-up reasons.

The helper must never take screenshots or collect semantic/style samples.

- [ ] **Step 2: Add a verified reverse traversal**

Add `_restore_reference_start(page, initial_state, settings)`. Traverse with
negative wheel steps and the existing script fallback. A document or nested
scroller is restored when its `top` is within two pixels of the initial value.
A virtual scroller is restored when its transform signature matches the
initial transform signature. Raise `ReferenceCaptureError` if the state cannot
be restored within the existing step bound.

- [ ] **Step 3: Reorder `_capture_loaded_page`**

The method order becomes:

```python
await page.wait_for_load_state(...)
await _wait_for_fonts_and_viewport_images(page, min(timeout_ms, 5000))
await page.wait_for_timeout(settings.warmup_ms)
initial_state, warmup_reasons = await _warm_reference_page(page, settings)
await _restore_reference_start(page, initial_state, settings)
await _wait_for_fonts_and_viewport_images(page, 2500)
# Existing top/middle/bottom capture traversal begins here.
```

Merge warm-up warnings into `skipped_reasons` and set:

```python
reset_strategy="wheel-prewarm-return-top"
```

- [ ] **Step 4: Update the existing warm-up assertion**

In `test_warmup_incremental_scroll_and_tiles_reveal_lazy_content`, change the
first capture assertion from zero wheel events to:

```python
self.assertEqual(captures[0][0], "top")
self.assertEqual(captures[0][1]["y"], 0)
self.assertGreater(captures[0][1]["wheels"], 0)
self.assertEqual(evidence.reset_strategy, "wheel-prewarm-return-top")
```

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```powershell
python -m pytest tests/builder_lab_cases/test_reference_crawler.py -q -k "two_pass or warmup or nested or virtual or native_document"
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit the implementation**

```powershell
git add builder_lab/reference_crawler.py tests/builder_lab_cases/test_reference_crawler.py
git commit -m "Add two-pass reference page capture"
```

### Task 4: Regression, deployment, and live Flowwow proof

**Files:**
- Modify only if evidence reveals a scoped defect.

- [ ] **Step 1: Run the full crawler test module**

```powershell
python -m pytest tests/builder_lab_cases/test_reference_crawler.py -q
```

Expected: zero failures.

- [ ] **Step 2: Run the Builder Lab regression set**

```powershell
python -m pytest tests/builder_lab_cases -q
python -m ruff check builder_lab scripts tests/builder_lab_cases
python -m compileall -q builder_lab scripts
git diff --check
```

Expected: all commands exit zero.

- [ ] **Step 3: Push the feature branch and deploy the exact commit**

```powershell
git push origin codex/gemini-technical-foundation
```

On the server, fast-forward `/root/ai_project` to that branch and run:

```bash
bash scripts/deploy_builder_lab.sh
```

- [ ] **Step 4: Run a real Flowwow capture without generating a widget**

Inside the deployed container, invoke the reference-capture script against
`https://about.flowwow.com/` for desktop with the production limits. Verify:

- the command exits successfully;
- top, middle, and bottom/last-observed screenshots exist;
- reset strategy is `wheel-prewarm-return-top`;
- lazy-image timeout is not a fatal error.

- [ ] **Step 5: Verify the user-facing Builder Lab**

Reload `http://127.0.0.1:18091/` in the in-app browser, confirm the current
build loads without console errors, and leave the form open without submitting
a generation on the user's behalf.
