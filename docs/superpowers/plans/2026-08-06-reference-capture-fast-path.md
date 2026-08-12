# Reference Capture Fast Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve concurrent desktop/mobile rendering, real wheel scrolling, lazy content, animations, and six reference screenshots while reducing reference capture from minutes to a bounded tens-of-seconds fast path.

**Architecture:** The single-page path will use one Crawlee `BrowserPool` and one Chromium process with two isolated pages. A fast sweep will trigger lazy content without a full quiet wait after every scroll step; expensive asset/layout settling will run only at top, middle, and bottom evidence anchors. Known third-party analytics traffic will be blocked before Python proxying, capture timings will be stored on the `reference.completed` event, and the existing Python guard remains fail-closed for all other traffic.

**Tech Stack:** Python 3.12, asyncio, Crawlee BrowserPool, Playwright Chromium, pytest/unittest, SQLAlchemy generation events, Docker Compose.

---

### Task 1: Fast sweep and anchor settling

**Files:**
- Modify: `builder_lab/reference_crawler.py`
- Test: `tests/builder_lab_cases/test_reference_crawler.py`

- [x] **Step 1: Write failing tests for sweep behavior**

Add tests proving that `_warm_reference_page` and the evidence pass do not call `_settle_scrolled_viewport` after every wheel step, that they still reach the end, and that settling occurs at top/middle/bottom anchors.

- [x] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
python -m pytest tests/builder_lab_cases/test_reference_crawler.py -k "fast_sweep or anchor_settle" -vv
```

Expected: failures showing the existing per-step settle calls.

- [x] **Step 3: Implement the minimal fast sweep**

Introduce a short bounded sweep pause derived from `scroll_delay_ms`, preserve `_scroll_once` wheel events and end detection, remove per-step `_settle_scrolled_viewport` calls, and settle only at evidence anchors. Continue collecting semantic/style samples at the three evidence anchors.

- [x] **Step 4: Preserve animation state in screenshots**

Change `_take_screenshot` to use `animations="allow"`; the crawler must observe live animation/reveal state rather than Playwright fast-forwarding finite animations or resetting infinite ones.

- [x] **Step 5: Run the focused tests and verify GREEN**

Run the focused command from Step 2 plus the existing lifecycle/end-detection tests.

### Task 2: One Chromium for desktop and mobile

**Files:**
- Modify: `builder_lab/reference_crawler.py`
- Test: `tests/builder_lab_cases/test_reference_crawler.py`

- [x] **Step 1: Write a failing browser-pool lifecycle test**

The test supplies a fake `BrowserPool`, opens desktop and mobile pages concurrently, verifies both pages come from the same pool, verifies viewport sizes `1920x1080` and `390x844`, and verifies both pages/pool close on success and failure.

- [x] **Step 2: Run the focused test and verify RED**

```powershell
python -m pytest tests/builder_lab_cases/test_reference_crawler.py -k "single_browser_pool" -vv
```

Expected: failure because the existing code calls `capture_reference_page`, which starts its own Playwright/Chromium instance per viewport.

- [x] **Step 3: Extract capture of an already-open page**

Add an internal helper that receives a Crawlee page, applies the requested viewport, installs the existing guarded context policy, tracing, navigation, capture, and cleanup. Keep public `capture_reference_page()` as the standalone compatibility wrapper.

- [x] **Step 4: Use Crawlee BrowserPool in the one-page path**

Create one `BrowserPool.with_default_plugin(browser_type="chromium", use_incognito_pages=True, max_open_pages_per_browser=2, service_workers="block")`, open two pages concurrently, and feed them into the extracted helper.

- [x] **Step 5: Run pool and existing concurrency tests and verify GREEN**

Run the new pool test and `test_single_page_viewports_start_concurrently`.

### Task 3: Block non-content analytics before proxying

**Files:**
- Modify: `builder_lab/reference_crawler.py`
- Test: `tests/builder_lab_cases/test_reference_crawler.py`

- [x] **Step 1: Write failing URL classification and route tests**

Cover representative Yandex Metrika, Google Analytics/Tag Manager, Mail.ru counter, VK retargeting, LinkedIn ads, Meta pixel, and DoubleClick URLs. Also prove same-origin content JS/CSS/images and third-party CDN assets remain allowed.

- [x] **Step 2: Run the focused tests and verify RED**

```powershell
python -m pytest tests/builder_lab_cases/test_reference_crawler.py -k "non_content_tracking" -vv
```

- [x] **Step 3: Implement conservative tracker blocking**

Add a bounded hostname/path classifier and call it at the start of `_guarded_context_route`. Record `analytics blocked` in capture telemetry and abort before `_bounded_stream_fetch`; do not block arbitrary third-party scripts or images.

- [x] **Step 4: Run route policy tests and verify GREEN**

Run the new tests plus all existing context-route and byte-limit tests.

### Task 4: Durable phase metrics

**Files:**
- Modify: `builder_lab/reference_crawler.py`
- Modify: `builder_lab/reference_pipeline.py`
- Modify: `builder_lab/worker.py`
- Modify: `builder_lab/generation_events.py`
- Test: `tests/builder_lab_cases/test_reference_pipeline.py`
- Test: `tests/builder_lab_cases/test_worker.py`
- Test: `tests/builder_lab_cases/test_generation_event_registry.py`

- [x] **Step 1: Write failing metric projection tests**

Define a bounded `capture_metrics` event object containing `total_ms` and per-viewport numeric phase timings. Verify non-numeric/private fields are discarded and the public payload remains bounded.

- [x] **Step 2: Run metric tests and verify RED**

```powershell
python -m pytest tests/builder_lab_cases/test_reference_pipeline.py tests/builder_lab_cases/test_worker.py tests/builder_lab_cases/test_generation_event_registry.py -k "capture_metrics" -vv
```

- [x] **Step 3: Carry metrics without polluting model context**

Add a defaulted `capture_metrics` field to `ReferenceAnalysisResult`, compute it from `ReferenceCrawlResult.pages`, allow `details.capture_metrics` on the `reference.completed` stage event, and persist/project the sanitized metrics independently of `reference_context`.

- [x] **Step 4: Record navigation and total capture timing**

Include navigation in each page timing map and store browser-pool elapsed time in the crawl metrics.

- [x] **Step 5: Run metric tests and verify GREEN**

Run the focused command from Step 2.

### Task 5: Full verification, release record, and production control run

**Files:**
- Modify: `docs/product-journal/2026-08.md`
- Optional evidence: `docs/release-evidence/`

- [x] **Step 1: Run focused browser/crawler tests**

```powershell
python -m pytest tests/builder_lab_cases/test_reference_crawler.py tests/builder_lab_cases/test_reference_pipeline.py tests/builder_lab_cases/test_generation_event_registry.py tests/builder_lab_cases/test_worker.py -q
```

- [x] **Step 2: Run static verification**

```powershell
python -m ruff check builder_lab/reference_crawler.py builder_lab/reference_pipeline.py builder_lab/worker.py builder_lab/generation_events.py tests/builder_lab_cases/test_reference_crawler.py tests/builder_lab_cases/test_reference_pipeline.py tests/builder_lab_cases/test_worker.py tests/builder_lab_cases/test_generation_event_registry.py
python -m compileall -q builder_lab tests/builder_lab_cases
git diff --check
```

- [x] **Step 3: Commit only the scoped implementation**

Stage only the crawler, pipeline, event, test, plan, and product-journal files. Preserve all unrelated Telegram, node_modules, output, and user changes.

- [x] **Step 4: Deploy the builder worker**

Copy the committed scoped files to `/root/ai_project`, rebuild only `builder-worker`, restart it, verify the worker boot log, and verify `https://kaigo.space/api/health` returns HTTP 200.

- [ ] **Step 5: Run one production reference capture and report timings**

Use the existing developer account/project workflow for one Mindbox capture. Verify the six screenshots, animation/lazy-load evidence, `reference.completed.capture_metrics`, total elapsed time, resource use, and absence of lease loss. Do not claim the target until these measurements complete.

Production control result on 2026-08-06: the six-screenshot acceptance gate remains open. Independent desktop and mobile captures completed with full coverage in 59.9 s and 32.2 s respectively, but a combined production crawl overloaded the 2 GB VPS and did not produce a complete six-frame artifact. Raw HTTP and native Chromium navigation to Mindbox were both sub-three-second, so network geography was ruled out as the primary bottleneck. The deployed worker is healthy at commit `5aee1f4`; the next control run must happen only after browser resource isolation/capacity is addressed. Full measurements and negative evidence are recorded in `docs/release-evidence/2026-08-06-reference-capture-fast-path.md`.
