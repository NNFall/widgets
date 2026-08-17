# AntiGravity Text Provider for Kaigo Builder — Design

**Date:** 2026-08-17  
**Status:** approved for an experimental production rollout  
**Owner:** Kaigo builder pipeline

## Objective

Use the already deployed AntiGravity Text API as an additional Kaigo model
provider without replacing the proven Codex path. The first experiment must
improve diversity and potentially reduce latency in the direction-board stage,
remain reversible by one environment flag, and preserve the existing model-call
audit and fallback semantics.

The deployed transport already exists:

`Kaigo builder (NL) -> https://kaigo.space/antigravity-api -> NL gateway -> SSH tunnel -> US AntiGravity provider -> isolated agy turn`

No second US wrapper or direct public US listener is required.

## First rollout boundary

AntiGravity is the primary provider only for `direction_candidate` in `direct`
and `express` modes. `run_direction_board()` already starts three independent
candidate calls with `asyncio.gather`, so one generation can use three of the
five available API slots without adding another concurrency mechanism.

The route for each candidate is:

1. `antigravity_text / gemini-3.7-flash-high`;
2. the existing Codex bridge target;
3. no hidden provider retry inside the AntiGravity adapter.

The direction judge, persona, artifact-generation stages, repair stages,
reference analysis, visual critics, and visual judge remain on their existing
routes in this first experiment. This isolates the quality comparison and keeps
all image-bearing requests away from the text-only HTTP contract.

## Provider contract

Create `AntigravityTextProvider`, implementing the existing `ModelProvider`
protocol and advertising:

- `images=False`;
- `structured_output=True`.

Request mapping to `POST /v1/respond`:

- `prompt <- ModelRequest.prompt`;
- `model <- ProviderTarget.model`;
- `reasoning_effort <- configured value`, which must agree with an effort suffix
  encoded by the model name;
- `native_tools="none"`;
- `response_format <- ModelRequest.response_schema` when present;
- no client tools, no native tools, no conversation reuse, and no images.

Response mapping:

- `text <- output_text`;
- `parsed <- json.loads(output_text)` followed by local Draft 2020-12 schema
  validation for structured requests;
- normalized input/output/thinking/cache usage;
- `request_id`, returned model, and provider identity
  `actual_provider="antigravity_cli"`;
- cost remains `unknown`: the target has no invented token price and the adapter
  does not claim a zero-cost call.

The adapter never logs the bearer key, prompt, response, or authorization
header. Cancellation propagates directly to `httpx`; the remote gateway already
cancels the provider turn and removes its disposable conversation/workspace.

## Failure mapping and fallback

- HTTP 401/403 -> `ProviderPermissionDenied`;
- HTTP 404 -> `ModelUnavailable`;
- HTTP 429 (`busy` or `rate_limited`) -> `ProviderQuotaExceeded`;
- HTTP 504 -> `ProviderTimeout`;
- HTTP 5xx / transport failure -> `ProviderUnavailable`;
- malformed JSON, empty output, invalid usage, wrong model identity, or schema
  mismatch -> `InvalidModelResponse`;
- image input -> `UnsupportedModelRequest` before dispatch.

All these codes are already eligible for `ModelRouter` fallback. The router,
not the adapter, owns fallback order, deadlines, cancellation settlement,
provenance, and `ModelCall` accounting.

## Configuration

New builder settings:

- `KAIGO_ANTIGRAVITY_API_ENABLED=false`;
- `KAIGO_ANTIGRAVITY_API_BASE_URL=https://kaigo.space/antigravity-api`;
- `KAIGO_ANTIGRAVITY_API_KEY` (required only when enabled, secret);
- `KAIGO_ANTIGRAVITY_API_MODEL=gemini-3.7-flash-high`;
- `KAIGO_ANTIGRAVITY_API_REASONING_EFFORT=high`;
- `KAIGO_ANTIGRAVITY_API_TIMEOUT_SECONDS=180`.

When enabled, the base URL must be HTTPS, the key/model/effort must be non-empty,
and model/effort must match when the model embeds `-low`, `-medium`, or `-high`.
The key is stored only in the durable root-owned Kaigo environment on NL. A
dedicated service key is created for this integration; only its hash is added to
the AntiGravity gateway configuration.

## Verification and rollout

1. TDD provider tests: request shape, schema parsing, all status mappings,
   timeout, cancellation, images, malformed output, usage, and secret-safe
   errors.
2. Configuration tests: disabled defaults and fail-closed enabled settings.
3. Runtime-router tests: only `direction_candidate` receives AntiGravity first;
   direction judge and all image roles remain unchanged; fallback reaches Codex.
4. Existing router/direction suites and worker configuration tests remain green.
5. An AntiGravity Worker reviewer independently checks the final diff.
6. Create a dedicated gateway key, deploy one pinned Kaigo worker image, enable
   the flag, and verify readiness/model-call provenance.
7. Submit a bare public HTTPS site URL with no custom brief, wait for a verified
   widget, publish it through the controlled canary account, and exercise the
   external HTTPS loader, open/close behavior, responsive view, chat reply,
   allowed-origin enforcement, stable link, and rollback path.

Rollback is configuration-first: disable `KAIGO_ANTIGRAVITY_API_ENABLED` and
restart only the builder worker. No database migration or artifact-format change
is introduced.

## Explicit non-goals

- Do not replace visual analysis or visual critics with a text-only API.
- Do not enable AntiGravity native read/full tools from the Kaigo builder.
- Do not add image generation to the HTTP contract in this change.
- Do not use the legacy `AntigravityEngine` / Google Interactions preview path.
- Do not route production chat traffic to AntiGravity in this experiment.
- Do not treat gateway health alone as proof; a real structured turn and a real
  published widget are required.
