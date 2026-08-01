# Adaptive Reference Capture Latency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Сохранить полный двухпроходный visual capture, но убрать последовательные дубли ожиданий и добавить фазовые измерения.

**Architecture:** Изменения остаются внутри `builder_lab/reference_crawler.py`. Reverse wheel traversal проверяет только scroll state и выполняет один settle в начале страницы; forward traversal использует один helper, который параллельно ждёт visual quiet и viewport assets. Pipeline не делает sitemap preflight при одной странице.

**Tech Stack:** Python 3.11, asyncio, Playwright, unittest/pytest, Ruff.

---

### Task 1: Bounded adaptive capture

**Files:**
- Modify: `builder_lab/reference_crawler.py`
- Test: `tests/builder_lab_cases/test_reference_crawler.py`

- [ ] **Step 1: Write RED tests for timing-safe behavior**

Добавить unit-level fake page tests, которые проверяют:

```python
async def test_restore_waits_for_full_settle_only_after_top_is_restored():
    # Three reverse scroll states, then restored top.
    # Assert _settle_scrolled_viewport is called exactly once.
    # Assert all wheel deltas are negative.

async def test_scrolled_viewport_waits_for_quiet_and_images_concurrently():
    # Both patched awaits block on one Event.
    # Assert both have started before Event is released.
    # Assert visual quiet minimum_ms equals settings.scroll_delay_ms.

async def test_fonts_ready_is_bounded_by_local_timeout():
    # document.fonts.ready never resolves.
    # Assert helper returns/raises within supplied timeout instead of hanging.

async def test_single_page_crawl_skips_sitemap_discovery():
    # Patch _sitemap_candidates to raise if called.
    # Crawl with max_pages=1 and assert capture succeeds.
```

Расширить существующий two-pass browser fixture: сохранить top/middle/bottom,
`reset_strategy` и lazy content, а также проверить новые phase timing keys и step
counters.

- [ ] **Step 2: Run RED tests**

Run:

```powershell
python -m pytest tests/builder_lab_cases/test_reference_crawler.py -q -k "restore_waits_for_full_settle or concurrently or fonts_ready_is_bounded or single_page_crawl_skips_sitemap"
```

Expected: новые тесты падают на текущем последовательном settle, безграничном
`document.fonts.ready` и unconditional sitemap discovery.

- [ ] **Step 3: Implement one bounded forward settle**

`_settle_scrolled_viewport` должен запускать visual quiet и viewport-image/font wait
в одном `asyncio.gather`, сохраняя image timeout как warning:

```python
quiet_task = asyncio.create_task(
    _wait_for_visual_quiet(
        page,
        minimum_ms=settings.scroll_delay_ms,
        quiet_ms=450,
        maximum_ms=min(2500, timeout_ms),
    )
)
image_task = asyncio.create_task(
    _wait_for_fonts_and_viewport_images(page, min(2500, timeout_ms))
)
results = await asyncio.gather(quiet_task, image_task, return_exceptions=True)
if isinstance(results[1], Exception):
    _append_unique_reason(skipped_reasons, image_timeout_reason)
```

Не оставлять отдельный `page.wait_for_timeout(settings.scroll_delay_ms)` перед этим
helper. Evidence pass должен использовать тот же helper с отдельным reason.

- [ ] **Step 4: Implement fast verified reset**

`_restore_reference_start` выполняет `_scroll_once` и повторно читает
`_SCROLL_STATE_SCRIPT` без `_settle_scrolled_viewport` на промежуточных шагах. После
доказанного возврата он один раз вызывает `_settle_scrolled_viewport` и повторно
проверяет `_scroll_state_is_restored`; иначе сохраняет fatal
`ReferenceCaptureError`.

- [ ] **Step 5: Bound fonts and skip unused sitemap work**

Обернуть `document.fonts.ready` в локальный timeout, не превышающий `timeout_ms`, до
`page.wait_for_function`. В `VisualReferenceCrawler.crawl` вызывать
`_sitemap_candidates` только если `self.limits.max_pages > 1`; иначе использовать
пустой tuple.

- [ ] **Step 6: Add phase timings and step counters**

Снять monotonic checkpoints вокруг load/initial settle, warm, reset, evidence и final
settle. Добавить в `timings_ms` только числовые значения и счётчики:

```python
{
    "load_and_initial_settle": ...,
    "warm_pass": ...,
    "reset_pass": ...,
    "evidence_pass": ...,
    "final_settle_and_screenshots": ...,
    "warm_steps": warm_steps,
    "reset_steps": reset_steps,
    "evidence_steps": evidence_steps,
    "total": ...,
}
```

- [ ] **Step 7: Run GREEN tests and performance fixture**

Run:

```powershell
python -m pytest tests/builder_lab_cases/test_reference_crawler.py -q -k "restore_waits_for_full_settle or concurrently or fonts_ready_is_bounded or single_page_crawl_skips_sitemap"
$sw=[Diagnostics.Stopwatch]::StartNew(); python -m pytest tests/builder_lab_cases/test_reference_crawler.py::BrowserLifecycleTests::test_two_pass_warmup_loads_offscreen_lazy_image_before_top_capture -q; $sw.Stop(); $sw.Elapsed.TotalSeconds
```

Expected: все тесты проходят; fixture быстрее 47,6 секунды при baseline 68,0 секунды.

- [ ] **Step 8: Run regression and static checks**

Run:

```powershell
python -m pytest tests/builder_lab_cases/test_reference_crawler.py tests/builder_lab_cases/test_reference_pipeline.py -q
python -m ruff check builder_lab/reference_crawler.py tests/builder_lab_cases/test_reference_crawler.py
python -m compileall -q builder_lab/reference_crawler.py
git diff --check
```

Expected: zero failures and zero lint/whitespace errors.

- [ ] **Step 9: Commit only the scoped files**

```powershell
git add builder_lab/reference_crawler.py tests/builder_lab_cases/test_reference_crawler.py docs/superpowers/specs/2026-08-01-adaptive-reference-capture-latency-design.md docs/superpowers/plans/2026-08-01-adaptive-reference-capture-latency.md
git commit -m "perf: bound reference capture settling"
```
