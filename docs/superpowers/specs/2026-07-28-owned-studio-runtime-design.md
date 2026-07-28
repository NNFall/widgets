# Owner-scoped Studio preview and chat runtime

## Goal

Serve durable Studio artifacts and chat through the authenticated SaaS application. The main application must not depend on the Basic-Auth Builder Lab or its in-memory `RunStore`.

## HTTP boundary

`GET /api/runs/{run_id}/preview/document?revision=N&channel=...` requires an authenticated owner session. An unauthenticated request returns `401`; an invalid UUID or a run outside the session's user and tenant scope returns `404`. `revision` must be a positive integer and `channel` must match the fixed preview runtime's 22–96 character channel grammar.

The endpoint loads the exact durable revision from `GenerationArtifact` when its quality is `accepted` or `verified`, otherwise it may load the exact matching `artifact.draft_staged` event. It reconstructs `WidgetArtifact`, runs `validate_artifact` again, and returns `409 preview_not_ready` when no valid exact revision exists. Successful responses use Python `build_preview_document`, an HTTP CSP header equal to `PREVIEW_CSP`, `Cache-Control: no-store`, and `X-Content-Type-Options: nosniff`.

`POST /api/runs/{run_id}/chat` requires the same owner scope plus the existing session CSRF token. Its JSON body contains a strict request ID, a non-empty message of at most 1,000 characters, and a positive revision. The route loads and validates the exact durable artifact before invoking chat. Chat failures use structured codes and explicit status codes, including `409 chat_not_ready` and `503 chat_not_configured`.

## Chat service

Routes depend only on an injected `ProjectChatService` protocol. `RoutedProjectChatService` owns bounded in-memory conversation state: a finite number of sessions, bounded history, response cache, one in-flight turn per session, idempotent request IDs, per-session and per-user rate windows, per-session request budgets, global concurrency, provider timeouts, and deterministic cleanup of pending tasks.

The authenticated aiohttp server session stores a random chat-session ID. Service scope includes tenant, user, run, and revision, preventing conversation reuse across owners or artifacts. Durable prompt context contains only the project's source URL and brief plus the validated artifact identity. Context and history are JSON-delimited as data. Prompts, responses, keys, and cookies are never logged.

The service calls `ModelRouter.generate` with role `chat_visitor`, mode `express`, and the durable run UUID. `SqlModelCallAudit` records provider, model, role, usage, pricing snapshot, and exact computed cost without prompt text. The route has no Gemini import or provider-specific branch.

## Production lifecycle and configuration

`AppConfig` includes bounded chat model, timeout, rate, capacity, concurrency, provider URL, and price configuration. API keys are optional fields with `repr=False`. The main application registers chat initialization after the database cleanup context, so `SqlModelCallAudit` receives the live session factory. When a provider key is configured, startup creates a Gemini adapter as the first provider, a `ModelRouter` policy for `chat_visitor:express`, and the routed service. Without a provider, routes remain available but chat returns explicit `503`.

The cleanup context closes service tasks and the router/provider. Partial startup failures close any resources already created before propagating the error.

## Frontend

SaaS Studio uses the owner-scoped preview document URL. Its existing parent `postMessage` bridge sends chat to the owner-scoped SaaS chat endpoint with the hydrated CSRF token. Legacy Builder Lab Studio continues to use Builder Lab preview and chat endpoints.

## Verification

Integration tests use a real aiohttp application and durable SQL records. They assert fixed runtime markers and launcher/chat elements in the returned document; 401, cross-owner 404, invalid input, 409 not-ready, and CSP/cache headers; and a complete chat turn through a fake `ModelProvider` behind the real `ModelRouter` and `SqlModelCallAudit`. Audit assertions cover role, run ID, token counts, pricing snapshot, and exact cost. Additional tests cover CSRF, idempotency, rate limits, unavailable-provider 503, and lifecycle closure. Frontend tests dispatch a real preview bridge message and assert the SaaS chat request path and CSRF header rather than only comparing a URL string.
