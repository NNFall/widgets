# Reference capture fast path — release evidence (2026-08-06)

## Outcome

The crawler fast path is implemented and deployed, and the production worker is healthy. The final six-screenshot production acceptance gate is **not passed**: desktop and mobile each complete independently, but the current 2 GB VPS becomes resource-bound when the full capture is exercised.

This is intentionally recorded as a partial delivery rather than a successful performance claim.

## Released changes

- `19929ee` — fast wheel sweep, anchor-only settling, live screenshot animations, one browser pool, tracker blocking, and durable capture metrics.
- `df686e4` — remove a redundant reference load gate.
- `becad01` — navigate on Chromium `commit` and wait for meaningful rendered content instead of the full browser load event.
- `19db535` — opt-in native Chromium transport in production while preserving guarded origin validation and host firewall protection.
- `43ca000` — coalesce duplicate DNS guard checks per origin.
- `5aee1f4` — make viewport concurrency configurable and serialize desktop/mobile on the small production worker.

Production image digest:

```text
sha256:5ec5e324ef758a0ce4eb26ebd1a1967cfdda839e03f10143a99bac92150dc233
```

Production settings:

```text
KAIGO_REFERENCE_NATIVE_TRANSPORT=true
KAIGO_REFERENCE_VIEWPORT_CONCURRENCY=1
```

## What the measurements show

Mindbox access from the VPS is not the primary bottleneck:

- host HTTP request: 0.68–0.88 s;
- request from the worker container: 0.66–0.80 s;
- native Chromium `DOMContentLoaded`: 2.12–2.46 s;
- guarded native capture navigation with `wait_until=commit`: 0.84–0.89 s.

Independent instrumented captures completed with full page coverage:

| Viewport | Total | Navigation | Initial render | Warm sweep | Reset | Evidence sweep | Final capture |
|---|---:|---:|---:|---:|---:|---:|---:|
| Desktop | 59.85 s | 0.89 s | 9.46 s | 19.29 s | 9.93 s | 18.97 s | 2.20 s |
| Mobile | 32.19 s | 0.84 s | 8.13 s | 9.93 s | 4.45 s | 8.44 s | 1.24 s |

The table shows that the dominant cost is our warm/reset/evidence lifecycle, not downloading the initial HTML.

## Negative production evidence

- A dual-viewport production attempt returned after 134.15 s with `crawl_failed` / `unknown crawl failure` and no complete page set.
- A sequential production attempt exceeded the 180 s control budget and did not produce a final artifact.
- During the dual capture, host load rose above 100 and the nearly full swap made SSH and health checks temporarily unavailable.
- The VPS has approximately 2 GB RAM and approximately 2 GB swap. An unrelated long-running PostgreSQL maintenance process was also visible on the host; it was not modified because it is outside this task's scope.
- No valid six-frame evidence directory was produced, so no screenshots are claimed or attached.

The worker was recovered without touching unrelated services. After recovery, `https://kaigo.space/api/health` returned HTTP 200 and the builder worker started normally.

## Verification performed

Focused RED-to-GREEN coverage was added for:

- `commit` navigation and rendered-document readiness;
- native browser transport policy;
- coalesced concurrent origin validation;
- serialized small-worker mode and default parallel mode;
- production firewall/Compose requirements;
- persisted and bounded `capture_metrics`.

Latest focused results included:

```text
navigation-related tests: 5 passed
native route tests: 2 passed
native lazy/warm-up integration cases: 2 passed in 63.78 s
viewport scheduling modes: 2 passed
deployment egress contract: 1 passed
ruff: passed
compileall: passed
git diff --check: passed
```

An attempted combined crawler/pipeline/event/worker/production-contract suite reached the 15-minute command timeout. It is therefore not reported as passing. Before the later production-specific changes, the earlier scoped suite completed with 77 passing tests; the later deltas were covered by the focused tests listed above.

## Correct next step

Do not add more arbitrary page waits. The next iteration should isolate Chromium capacity first:

1. run reference capture in a dedicated browser worker or increase the VPS memory;
2. set explicit container CPU/memory limits and keep one viewport active on small workers;
3. persist phase checkpoints and partial capture diagnostics so a killed crawl never becomes `unknown crawl failure`;
4. collapse warm sweep plus reset plus evidence sweep into one evidence-producing traversal where animation semantics allow it;
5. only then run one new Mindbox control and require all six screenshots plus `reference.completed.capture_metrics` before declaring the fast path complete.
