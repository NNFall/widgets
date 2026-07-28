# Owner-scoped Studio Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve durable Studio previews and audited chat through authenticated, owner-scoped SaaS endpoints.

**Architecture:** `app.projects.routes` owns HTTP authentication, CSRF, ownership, durable artifact selection, and preview rendering. A new `app.projects.chat` module owns a bounded provider-neutral service backed by `ModelRouter`; `app.server` creates and closes it after database initialization. SaaS Studio selects these endpoints while legacy Studio retains Builder Lab endpoints.

**Tech Stack:** aiohttp, aiohttp-session, SQLAlchemy async ORM, PostgreSQL/SQLite integration tests, ModelRouter/SqlModelCallAudit, React/TypeScript/Vitest.

---

### Task 1: Durable preview document contract

**Files:**
- Modify: `tests/saas_cases/test_project_routes.py`
- Modify: `app/projects/routes.py`

- [ ] **Step 1: Write failing route tests**

Add a durable accepted artifact and assert:

```python
response = await client.get(
    f"/api/runs/{run_id}/preview/document?revision=1&channel=channel-1234567890abcdef"
)
assert response.status == 200
document = await response.text()
assert "data-kaigo-runtime-launcher" in document
assert "chat.request" in document
assert response.headers["Content-Security-Policy"] == PREVIEW_CSP
assert response.headers["Cache-Control"] == "no-store"
assert response.headers["X-Content-Type-Options"] == "nosniff"
```

Cover unauthenticated `401`, cross-owner `404`, invalid revision/channel `400`, absent exact revision `409`, and exact restorable draft rendering.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/saas_cases/test_project_routes.py -k "preview_document" -q`

Expected: `404` because the document route does not exist.

- [ ] **Step 3: Implement exact artifact selection and rendering**

Refactor the existing preview selector to return a validated `WidgetArtifact` plus serialized metadata. Add strict query parsing and:

```python
return web.Response(
    text=build_preview_document(candidate, channel_id=channel),
    content_type="text/html",
    charset="utf-8",
    headers={
        "Content-Security-Policy": PREVIEW_CSP,
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    },
)
```

- [ ] **Step 4: Run focused preview tests and verify GREEN**

Run the command from Step 2; expect all selected tests to pass.

### Task 2: Provider-neutral bounded chat service

**Files:**
- Create: `app/projects/chat.py`
- Create: `tests/saas_cases/test_project_chat.py`

- [ ] **Step 1: Write failing service tests**

Construct a fake `ModelProvider` behind a real `ModelRouter` and `SqlModelCallAudit`. Assert an idempotent duplicate request calls the provider once; conflicting text returns `409`; a second request beyond the configured rate returns `429`; and `close()` closes the router/provider and pending tasks.

The successful test must query `ModelCall` and assert:

```python
assert call.role == "chat_visitor"
assert call.run_id == run_id
assert (call.input_tokens, call.output_tokens, call.thinking_tokens) == (11, 7, 2)
assert call.cost_microusd == 50
assert call.pricing_snapshot == {
    "currency": "USD",
    "billing_unit_tokens": 1_000_000,
    "input_price_microusd_per_million": 2_000_000,
    "output_price_microusd_per_million": 4_000_000,
}
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/saas_cases/test_project_chat.py -q`

Expected: import failure for the missing project chat module.

- [ ] **Step 3: Implement the injected service contract**

Define `ProjectChatService`, `ProjectChatReply`, `ProjectChatError`, and `RoutedProjectChatService`. Bound sessions, history, cached IDs, pending turns, rates, concurrency, timeout, and response length. Route every model call through:

```python
await router.generate(
    role="chat_visitor",
    mode="express",
    run_id=run_id,
    request=ModelRequest(prompt=prompt, temperature=0.35),
    timeout_seconds=timeout_seconds,
)
```

Do not log prompts, replies, cookies, or secrets.

- [ ] **Step 4: Run service tests and verify GREEN**

Run the command from Step 2; expect all tests to pass.

### Task 3: Owner-scoped chat route

**Files:**
- Modify: `tests/saas_cases/test_project_routes.py`
- Modify: `app/projects/routes.py`

- [ ] **Step 1: Write failing route integration tests**

Inject `RoutedProjectChatService` and post the preview bridge payload with session CSRF. Assert the reply, provider call, durable context markers, and SQL audit row. Add `401`, CSRF `403`, cross-owner `404`, invalid body `400`, missing exact artifact `409`, and missing service `503` cases. Repeat the same request ID to prove route-level idempotency.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/saas_cases/test_project_routes.py -k "run_chat" -q`

Expected: `404` because the SaaS chat route is absent.

- [ ] **Step 3: Implement the route**

Validate owner and artifact before reading the injected service. Store a random `project_chat_session_id` in the authenticated aiohttp session. Build scope from tenant/user/run/revision and invoke the protocol. Map `ProjectChatError` to structured JSON without diagnostic/provider text.

- [ ] **Step 4: Run route tests and verify GREEN**

Run the command from Step 2; expect all selected tests to pass.

### Task 4: Production configuration and lifecycle

**Files:**
- Modify: `app/config.py`
- Modify: `app/server.py`
- Modify: `.env.example`
- Create: `tests/saas_cases/test_project_chat_lifecycle.py`

- [ ] **Step 1: Write failing configuration/lifecycle tests**

Assert bounded configuration rejects nonpositive timeouts, invalid limits, blank configured models, and negative prices. Start the cleanup context with a fake provider factory, assert the service is installed only after the session factory exists, and assert cleanup closes the provider. Simulate construction failure after provider creation and assert it still closes.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/saas_cases/test_project_chat_lifecycle.py -q`

Expected: missing config fields and lifecycle functions.

- [ ] **Step 3: Implement validated config and failure-safe lifecycle**

Add secret config with `repr=False`, model/base URL, timeout, session/rate/capacity/concurrency settings, and input/output microusd rates. Build a Gemini provider only when an API key exists, wrap it in `ModelRouter` with `ModelPolicy(prompt_version="chat-visitor-v1", targets=(...))` and `SqlModelCallAudit`, then create the routed service. Register the cleanup context after `init_db_signals(app)`. Close the router/provider if any later construction step raises.

- [ ] **Step 4: Run lifecycle tests and verify GREEN**

Run the command from Step 2; expect all tests to pass.

### Task 5: SaaS frontend preview and chat bridge

**Files:**
- Modify: `frontend/src/studio/SaasStudioFlow.test.tsx`
- Modify: `frontend/src/studio/api.ts`
- Modify: `frontend/src/studio/useBuilderRun.ts`
- Modify: `frontend/src/studio/StudioPage.tsx`
- Modify: `frontend/src/studio/StudioPreview.tsx`

- [ ] **Step 1: Write failing browser-contract tests**

Assert the SaaS iframe points to `/api/runs/{id}/preview/document` and not `/builder/`. Dispatch a `chat.request` `MessageEvent` from the iframe and assert a real fetch to `/api/runs/{id}/chat` with `credentials: include`, `X-CSRF-Token`, exact revision/request ID/message, and no fake reply path. Keep the legacy bridge expectations unchanged.

- [ ] **Step 2: Run tests and verify RED**

Run: `cmd /c node_modules\.bin\vitest.cmd run src\studio\SaasStudioFlow.test.tsx --pool=forks --maxWorkers=1 --reporter=verbose`

Expected: SaaS iframe path remains `/builder/` and chat lacks SaaS CSRF.

- [ ] **Step 3: Implement mode-aware frontend API and bridge**

Expose the hydrated CSRF token from the SaaS run controller. Build a relative owner-scoped preview document URL. Route SaaS chat through `saasRequestJson` with `X-CSRF-Token`; keep legacy `builderUrl` and `X-Kaigo-Chat` behavior.

- [ ] **Step 4: Run focused frontend tests and verify GREEN**

Run the command from Step 2; expect all tests to pass.

### Task 6: Regression verification and scoped delivery

**Files:**
- Verify all files above

- [ ] **Step 1: Run backend focused and adjacent tests**

Run: `python -m pytest tests/saas_cases/test_project_routes.py tests/saas_cases/test_project_chat.py tests/saas_cases/test_project_chat_lifecycle.py tests/saas_cases/test_model_router.py -q`

Expected: all tests pass.

- [ ] **Step 2: Run frontend focused and legacy tests**

Run the four Studio/API/accessibility Vitest files with one worker. Expected: all tests pass.

- [ ] **Step 3: Run static and build verification**

Run `npm run typecheck`, `npm run lint`, and `npm run build` from `frontend`. Expect exit code 0; the known Vite chunk-size warning is non-blocking.

- [ ] **Step 4: Audit scope and commit**

Run `git diff --check`, inspect staged names, and stage only the owner-scoped runtime backend/frontend/tests/config hunks. Commit:

```text
fix: serve durable Studio runtime from SaaS
```
