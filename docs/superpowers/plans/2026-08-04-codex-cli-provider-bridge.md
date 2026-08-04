# Codex CLI Provider Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a feature-flagged Kaigo model provider that runs persistent `gpt-5.6-luna` conversations through the authenticated Codex CLI on the NL host, supports structured output and image review, limits the host to three concurrent turns, archives completed Codex tasks, and retains a safe Kaigo-owned JSONL history.

**Architecture:** A host-side `aiohttp` service owns Codex CLI subprocesses, SQLite thread mappings, per-conversation locks, a three-slot semaphore, timeout cleanup, and safe event logs. The containerized builder reaches it through a bind-mounted Unix socket using an `httpx` provider adapter; the existing `ModelRouter` supplies run and invocation lineage in internal request metadata and keeps current providers as fallbacks. The bridge is opt-in, uses read-only/no-approval Codex execution, and records subscription-backed calls with unknown monetary cost instead of a false zero.

**Tech Stack:** Python 3.11+, asyncio, aiohttp, httpx Unix-domain-socket transport, SQLite, Codex CLI JSONL mode, pytest, Docker Compose, systemd.

---

## Task 1: Preserve router lineage and honest unknown pricing

**Files:**
- Modify: `app/models/router.py`
- Test: `tests/saas_cases/test_model_router.py`

- [ ] **Step 1: Write failing tests for internal lineage metadata and unknown pricing**

Add tests proving that the provider receives a copied `ModelRequest.metadata` containing `_kaigo_run_id`, `_kaigo_role`, `_kaigo_mode`, `_kaigo_stage`, `_kaigo_operation`, `_kaigo_semantic_attempt`, `_kaigo_candidate_id`, and `_kaigo_persona`, while caller metadata remains unchanged. Add a test where a `ProviderTarget` has no rate card and a successful usage-bearing call is audited with `cost_state == "unknown"` and `cost_microusd is None`.

- [ ] **Step 2: Run the focused tests and confirm the expected failures**

Run: `python -m pytest -q tests/saas_cases/test_model_router.py -k "lineage_metadata or unknown_pricing"`

Expected: FAIL because router metadata is not yet injected and `ProviderTarget` currently requires integer rates.

- [ ] **Step 3: Implement the minimal router changes**

Allow the two primary rate fields on `ProviderTarget` to be `int | None`. Before each provider invocation, construct a new `ModelRequest` using `dataclasses.replace` and merge only the internal lineage keys into a copied metadata mapping. Keep prompt and images unchanged and never write them to the accounting audit.

- [ ] **Step 4: Run focused and baseline router tests**

Run: `python -m pytest -q tests/saas_cases/test_model_router.py`

Expected: PASS.

- [ ] **Step 5: Commit the router contract**

Run: `git add app/models/router.py tests/saas_cases/test_model_router.py && git commit -m "feat: expose safe model invocation lineage"`

## Task 2: Build persistent bridge state and safe event history

**Files:**
- Create: `tools/kaigo_codex_bridge/__init__.py`
- Create: `tools/kaigo_codex_bridge/state.py`
- Test: `tests/codex_bridge_cases/test_state.py`

- [ ] **Step 1: Write failing state-store tests**

Cover creating and reopening SQLite state, mapping `(run_id, conversation_key)` to one Codex thread id, rejecting unsafe identifiers, enumerating a run's active mappings, marking a mapping archived, and appending safe JSONL events that contain no prompt, model output, secrets, or reasoning text.

- [ ] **Step 2: Run the state tests and confirm import failure**

Run: `python -m pytest -q tests/codex_bridge_cases/test_state.py`

Expected: FAIL because `tools.kaigo_codex_bridge.state` does not exist.

- [ ] **Step 3: Implement `BridgeStateStore`**

Use `sqlite3` with a small transaction per operation, a unique key on `(run_id, conversation_key)`, UTC timestamps, and explicit lifecycle values `active` and `archived`. Store only thread ids and safe operational metadata. Append JSONL records with an allowlisted schema: timestamp, event name, run id, conversation key, thread id, model, token counts, duration, and typed error code.

- [ ] **Step 4: Run state tests**

Run: `python -m pytest -q tests/codex_bridge_cases/test_state.py`

Expected: PASS.

- [ ] **Step 5: Commit persistent bridge state**

Run: `git add tools/kaigo_codex_bridge tests/codex_bridge_cases/test_state.py && git commit -m "feat: persist Codex bridge sessions safely"`

## Task 3: Implement Codex CLI runner with session resume, images, and cleanup

**Files:**
- Create: `tools/kaigo_codex_bridge/config.py`
- Create: `tools/kaigo_codex_bridge/runner.py`
- Test: `tests/codex_bridge_cases/test_runner.py`

- [ ] **Step 1: Write failing runner tests**

Test exact command construction for a new turn and `codex exec resume`, Luna Max selection, JSON output, read-only sandbox, disabled web/tools/apps/multi-agent options, stdin prompt delivery, response-schema file use, repeated `--image` arguments, parsing `thread.started`, final `agent_message`, and `turn.completed` usage. Test the global three-slot semaphore, single-writer lock per `(run_id, conversation_key)`, typed invalid-output errors, and process-group termination after timeout.

- [ ] **Step 2: Run runner tests and confirm failure**

Run: `python -m pytest -q tests/codex_bridge_cases/test_runner.py`

Expected: FAIL because the runner is absent.

- [ ] **Step 3: Implement configuration and runner**

Read configuration from `KAIGO_CODEX_*` variables with safe defaults: executable `codex`, model `gpt-5.6-luna`, reasoning `max`, max concurrency `3`, timeout `900`, state root `/var/lib/kaigo/codex-bridge`. Feed prompts through stdin, place temporary schemas and PNG/JPEG inputs in a per-turn private directory, start subprocesses in their own process group, parse only documented JSONL event types, and archive threads with `codex archive <thread_id>` when a run is finalized. Never persist prompt text, output text, or reasoning events in the bridge event log.

- [ ] **Step 4: Run runner and state tests**

Run: `python -m pytest -q tests/codex_bridge_cases/test_runner.py tests/codex_bridge_cases/test_state.py`

Expected: PASS.

- [ ] **Step 5: Commit the runner**

Run: `git add tools/kaigo_codex_bridge tests/codex_bridge_cases && git commit -m "feat: run persistent Luna Max conversations"`

## Task 4: Expose a private Unix-socket bridge service

**Files:**
- Create: `tools/kaigo_codex_bridge/service.py`
- Create: `scripts/run_codex_bridge.py`
- Test: `tests/codex_bridge_cases/test_service.py`

- [ ] **Step 1: Write failing HTTP contract tests**

Cover `GET /health`, `POST /v1/turn`, and `POST /v1/runs/{run_id}/complete`; validate required fields and base64 images, return the final text/parsed JSON/usage/thread id, map runner failures to stable codes and retryable HTTP statuses, and prove completion archives every active thread for the run.

- [ ] **Step 2: Run service tests and confirm failure**

Run: `python -m pytest -q tests/codex_bridge_cases/test_service.py`

Expected: FAIL because the service is absent.

- [ ] **Step 3: Implement the Unix-socket API**

Build an `aiohttp.web.Application` with a startup cleanup for a stale socket and restrictive socket permissions. Enforce request-size and image-count limits, UUID run ids, bounded conversation keys, and local-only Unix-socket transport. The service entry point must handle SIGTERM/SIGINT and close state cleanly.

- [ ] **Step 4: Run bridge tests**

Run: `python -m pytest -q tests/codex_bridge_cases`

Expected: PASS.

- [ ] **Step 5: Commit the service**

Run: `git add tools/kaigo_codex_bridge scripts/run_codex_bridge.py tests/codex_bridge_cases && git commit -m "feat: expose private Codex bridge service"`

## Task 5: Add the Kaigo model-provider adapter

**Files:**
- Create: `app/models/providers/codex_bridge.py`
- Test: `tests/saas_cases/test_codex_bridge_provider.py`

- [ ] **Step 1: Write failing provider tests**

Use `httpx.MockTransport` or an injectable client to prove prompt, images, schema, model, and safe lineage metadata are serialized correctly; conversation keys are deterministic for build candidates, judges, critic personas, repairs, and reference analysis; valid structured output populates `ModelResponse.parsed`; token usage and actual provider/model are preserved; and bridge error codes become existing retryable `ModelProviderError` subclasses without leaking response bodies.

- [ ] **Step 2: Run provider tests and confirm import failure**

Run: `python -m pytest -q tests/saas_cases/test_codex_bridge_provider.py`

Expected: FAIL because the provider does not exist.

- [ ] **Step 3: Implement `CodexBridgeProvider`**

Use `httpx.AsyncHTTPTransport(uds=...)`, declare image and structured-output capabilities, derive the stable conversation key from router lineage, base64-encode images only in transit, validate the service response, and return `ModelResponse` with `actual_provider="codex_cli"`, the actual model, usage, and no false billing signal.

- [ ] **Step 4: Run provider and router tests**

Run: `python -m pytest -q tests/saas_cases/test_codex_bridge_provider.py tests/saas_cases/test_model_router.py`

Expected: PASS.

- [ ] **Step 5: Commit the provider**

Run: `git add app/models/providers/codex_bridge.py tests/saas_cases/test_codex_bridge_provider.py && git commit -m "feat: add Codex CLI model provider"`

## Task 6: Route builder roles through Codex behind a feature flag

**Files:**
- Modify: `builder_lab/config.py`
- Modify: `scripts/run_builder_worker.py`
- Modify: `tests/builder_lab_cases/test_config.py`
- Modify: `tests/builder_lab_cases/test_worker.py`
- Modify: `.env.example`

- [ ] **Step 1: Write failing configuration and policy tests**

Test defaults with the bridge disabled, strict validation of socket/model/timeout settings when enabled, successful router construction without a Gemini key when Codex is enabled, a primary Codex target with unknown pricing for all supported builder and visual roles, and retention of configured AgentRouter/Gemini/ZenMux targets as fallbacks when present.

- [ ] **Step 2: Run focused tests and confirm failure**

Run: `python -m pytest -q tests/builder_lab_cases/test_config.py tests/builder_lab_cases/test_worker.py -k "codex or runtime_router"`

Expected: FAIL because Codex configuration and routing are absent.

- [ ] **Step 3: Implement feature-flagged routing**

Add `KAIGO_CODEX_BRIDGE_ENABLED`, `KAIGO_CODEX_BRIDGE_SOCKET_PATH`, `KAIGO_CODEX_BRIDGE_TIMEOUT_SECONDS`, and `KAIGO_CODEX_BRIDGE_MODEL`. Register `CodexBridgeProvider` only when enabled. Put one Codex target first for generation, direction, review, repair, reference, and visual roles; add existing providers only when their credentials are present. Preserve the existing routing behavior byte-for-byte when the feature is disabled.

- [ ] **Step 4: Run configuration, worker, provider, and router tests**

Run: `python -m pytest -q tests/builder_lab_cases/test_config.py tests/builder_lab_cases/test_worker.py tests/saas_cases/test_codex_bridge_provider.py tests/saas_cases/test_model_router.py`

Expected: PASS.

- [ ] **Step 5: Commit builder integration**

Run: `git add builder_lab/config.py scripts/run_builder_worker.py tests/builder_lab_cases/test_config.py tests/builder_lab_cases/test_worker.py .env.example && git commit -m "feat: route builder through Codex bridge"`

## Task 7: Package, deploy, and verify on the NL host

**Files:**
- Create: `deploy/systemd/kaigo-codex-bridge.service`
- Modify: `docker-compose.yml`
- Create: `docs/release-evidence/codex-cli-provider-bridge.md`
- Modify: `docs/product-journal/2026-08.md`

- [ ] **Step 1: Write a failing Compose contract test**

Add a focused test to `tests/builder_lab_cases/test_worker.py` or a new `tests/deployment_cases/test_codex_bridge_deployment.py` that loads Compose YAML and verifies the builder worker receives the bridge environment and read-write Unix-socket directory while the bridge state directory is not mounted into the worker container.

- [ ] **Step 2: Run the deployment test and confirm failure**

Run: `python -m pytest -q tests/deployment_cases/test_codex_bridge_deployment.py`

Expected: FAIL because Compose and the systemd unit do not yet contain the bridge.

- [ ] **Step 3: Add host service and socket mount**

Create a hardened systemd unit that runs `scripts/run_codex_bridge.py` from `/root/ai_project`, restarts on failure, and owns `/run/kaigo-codex` plus `/var/lib/kaigo/codex-bridge`. Add the socket directory and four `KAIGO_CODEX_*` variables to `builder-worker`; keep the feature flag false by default.

- [ ] **Step 4: Run the complete local verification set**

Run: `python -m pytest -q tests/codex_bridge_cases tests/saas_cases/test_codex_bridge_provider.py tests/saas_cases/test_model_router.py tests/builder_lab_cases/test_config.py tests/builder_lab_cases/test_worker.py tests/deployment_cases/test_codex_bridge_deployment.py`

Expected: PASS.

- [ ] **Step 5: Update Codex CLI and deploy the feature disabled**

On `root@5.129.236.90`, record `codex --version`, update `@openai/codex` to the latest published version, record the new version, deploy the code and unit, start `kaigo-codex-bridge.service`, and verify the Unix socket health endpoint before enabling builder routing.

- [ ] **Step 6: Run real Luna Max smoke tests**

Through the private bridge, run one structured text turn, resume the same conversation with a correction request, run one PNG visual-review turn, and launch three independent delayed turns to prove the concurrency cap without exceeding it. Verify the response model, thread reuse, usage parsing, safe JSONL records, and service recovery after a deliberately short timeout.

- [ ] **Step 7: Enable the provider and test one non-production generation path**

Enable `KAIGO_CODEX_BRIDGE_ENABLED=true` for the builder worker, recreate only the bridge and worker services, verify readiness, and run a bounded test generation. If the Codex target fails, verify the typed fallback path and that the last available widget artifact remains available.

- [ ] **Step 8: Verify archive cleanup**

Finalize the test run through `/v1/runs/{run_id}/complete`, verify its Codex thread leaves active history, confirm the SQLite mapping is archived, and confirm the Kaigo safe JSONL operational history remains.

- [ ] **Step 9: Document evidence and journal the verified result**

Write exact CLI versions, service health, thread ids, timings, usage, concurrency evidence, archive evidence, and any remaining limitations to `docs/release-evidence/codex-cli-provider-bridge.md`. Append a concise Russian update to the existing August product journal without overwriting unrelated edits.

- [ ] **Step 10: Commit deployment and evidence**

Run: `git add deploy/systemd/kaigo-codex-bridge.service docker-compose.yml tests/deployment_cases/test_codex_bridge_deployment.py docs/release-evidence/codex-cli-provider-bridge.md docs/product-journal/2026-08.md && git commit -m "deploy: add Codex bridge canary"`

## Task 8: Final regression and operational handoff

**Files:**
- Modify: `docs/release-evidence/codex-cli-provider-bridge.md`

- [ ] **Step 1: Run the complete related regression suite**

Run: `python -m pytest -q tests/codex_bridge_cases tests/saas_cases/test_openai_compatible_provider.py tests/saas_cases/test_codex_bridge_provider.py tests/saas_cases/test_model_router.py tests/builder_lab_cases/test_config.py tests/builder_lab_cases/test_worker.py tests/deployment_cases/test_codex_bridge_deployment.py`

Expected: PASS.

- [ ] **Step 2: Inspect the scoped diff and secrets**

Run: `git diff --check` and `git diff --stat HEAD~6..HEAD`; search changed files for API-key prefixes and private prompt/output content. Confirm unrelated Telegram and user-owned dirty files were not staged or modified by this implementation.

- [ ] **Step 3: Record final evidence**

Append the final test command/results, deployed commit, service status, feature-flag state, rollback command, and known constraints to the release-evidence document.

- [ ] **Step 4: Commit final evidence if changed**

Run: `git add docs/release-evidence/codex-cli-provider-bridge.md && git commit -m "docs: record Codex bridge verification"`

