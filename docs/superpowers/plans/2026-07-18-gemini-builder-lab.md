# Gemini Builder Lab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an isolated, loopback-only Kaigo laboratory that creates premium widget drafts through real staged Gemini calls, validates every candidate before preview, exposes real progress over SSE, and supports a safely bounded Antigravity comparison adapter.

**Architecture:** Add a new `builder_lab` package and standalone aiohttp entry point without registering routes in the production application. Provider adapters emit a common artifact contract; a deterministic trust boundary validates artifacts; an in-memory orchestrator commits only valid revisions; a fixed Kaigo preview document renders generated HTML/CSS inside a sandboxed iframe. Tests use `unittest` and injected fake provider clients so the complete control flow is deterministic before any live credentialed smoke test.

**Tech Stack:** Python 3.11+, aiohttp, google-genai, dataclasses, asyncio, standard-library HTML/tar/JSON tooling, unittest, Docker Compose, Gemini GenerateContent and Interactions APIs.

## Execution status (2026-07-18)

- Tasks 1–11 are implemented on `codex/gemini-technical-foundation` through
  commit `0a56c24`.
- Verification: 90 tests, `compileall`, isolated `pip check`, Compose config,
  desktop/mobile Playwright review, and zero browser console warnings/errors.
- Task 12 (GitHub push, live Gemini evidence, loopback server deployment, and
  production regression smoke) remains in progress until remote evidence is
  recorded below.

---

## Execution rules

- Work only in `D:\papka for all\work\kaigo.widgets\work\gemini-technical-foundation` on `codex/gemini-technical-foundation`.
- Preserve existing production routes, widget rows, asset rows, nginx behavior, and the static site.
- Use the external virtual environment `D:\papka for all\work\kaigo.widgets\.venvs\gemini-builder-lab`; do not add a repository-local `.venv`.
- For every task, run the named failing test first, observe the expected failure, implement the smallest coherent behavior, and rerun the focused test before the full suite.
- Never put a Gemini key, SSH password, protected proxy prefix, or raw provider error in committed files or browser responses.
- Commit after each coherent green task. Push only after the complete local verification gate.

## Task 1: Establish the package, dependency, and typed contracts

**Files:**

- Modify: `requirements.txt`
- Create: `builder_lab/__init__.py`
- Create: `builder_lab/models.py`
- Create: `builder_lab/config.py`
- Create: `tests/__init__.py`
- Create: `tests/builder_lab_cases/__init__.py`
- Create: `tests/builder_lab_cases/test_models.py`
- Create: `tests/builder_lab_cases/test_config.py`

- [ ] Create the external virtual environment and install the existing requirements plus the current supported `google-genai` package:

  ```powershell
  py -3 -m venv "D:\papka for all\work\kaigo.widgets\.venvs\gemini-builder-lab"
  & "D:\papka for all\work\kaigo.widgets\.venvs\gemini-builder-lab\Scripts\python.exe" -m pip install --upgrade pip
  & "D:\papka for all\work\kaigo.widgets\.venvs\gemini-builder-lab\Scripts\python.exe" -m pip install -r requirements.txt google-genai
  ```

- [ ] Add model tests that prove JSON round-tripping, supported engines/stages, monotonic revision input, safe public error serialization, and default request normalization.
- [ ] Add config tests that prove the default host is `127.0.0.1`, port is `8091`, direct model is `gemini-3.5-flash`, temperature is `0.9`, repairs are bounded, secrets are required only when a provider is actually constructed, and non-loopback binding is rejected unless `KAIGO_BUILDER_LAB_ALLOW_REMOTE=true`.
- [ ] Run the tests and verify they fail because the package does not exist:

  ```powershell
  & "D:\papka for all\work\kaigo.widgets\.venvs\gemini-builder-lab\Scripts\python.exe" -m unittest tests.builder_lab_cases.test_models tests.builder_lab_cases.test_config -v
  ```

- [ ] Implement frozen enums and dataclasses for `EngineName`, `Stage`, `RunStatus`, `BuilderRequest`, `TokenUsage`, `ValidationIssue`, `WidgetArtifact`, `BuilderEvent`, and `BuilderRunSnapshot`. Each type must expose explicit `to_dict`/`from_dict` methods; provider exceptions must map to the stable public error categories from the design.
- [ ] Implement `BuilderLabConfig.from_env()` with explicit parsing, numeric bounds, Gemini endpoint settings, Antigravity limits, and loopback enforcement.
- [ ] Add `google-genai>=2.0.0,<3.0.0` to `requirements.txt`, install from the updated file, and rerun the focused tests.
- [ ] Commit:

  ```powershell
  git add requirements.txt builder_lab tests
  git commit -m "feat: add builder lab contracts"
  ```

## Task 2: Build the deterministic artifact trust boundary

**Files:**

- Create: `builder_lab/validation.py`
- Create: `tests/builder_lab_cases/test_validation.py`

- [ ] Write tests for a known-good artifact and rejection of: unsupported schema versions, non-monotonic revisions, missing semantic regions, malformed or oversized HTML, excessive DOM nodes, forbidden elements, inline event handlers, external links/forms, unsafe `data:` values, unscoped CSS, `@import`, `url()`, excessive stylesheet size, excessive animation count/duration/iterations, and motion without `prefers-reduced-motion`.
- [ ] Assert that validation returns stable issue codes and field paths, not provider prose, and that identical issue sets have an identical fingerprint.
- [ ] Run the focused test and observe import/test failures:

  ```powershell
  & "D:\papka for all\work\kaigo.widgets\.venvs\gemini-builder-lab\Scripts\python.exe" -m unittest tests.builder_lab_cases.test_validation -v
  ```

- [ ] Implement a standard-library `HTMLParser` validator with fixed element/attribute allowlists, URL checks, DOM limits, required `data-region` values, accessible launcher/composer labels, and balanced-tag checks.
- [ ] Implement CSS validation using conservative deterministic scanning: every selector must remain under `.kaigo-widget`; disallow document selectors, external resources, browser escape primitives, and unbounded motion; require a reduced-motion media query whenever animation/keyframes are present.
- [ ] Return all discovered `ValidationIssue` objects in deterministic order and add `issue_fingerprint()`.
- [ ] Rerun focused and current full suites, then commit:

  ```powershell
  git add builder_lab/validation.py tests/builder_lab_cases/test_validation.py
  git commit -m "feat: validate generated widget artifacts"
  ```

## Task 3: Safely collect Antigravity snapshots

**Files:**

- Create: `builder_lab/snapshots.py`
- Create: `tests/builder_lab_cases/test_snapshots.py`

- [ ] Write in-memory tar tests proving acceptance of exactly `out/widget-artifact.json` and `out/build-report.json`, and rejection of traversal paths, absolute paths, drive-qualified paths, symlinks, hard links, devices, undeclared imported paths, duplicate declared paths, excessive member counts, excessive individual files, excessive total bytes, and invalid JSON.
- [ ] Run the focused test and observe the missing implementation failure.
- [ ] Implement streaming tar inspection without calling `extractall()`. Normalize every POSIX member path, read only regular declared files, enforce configured byte/count limits before JSON decoding, and return a typed artifact plus a sanitized build-report dictionary.
- [ ] Rerun focused and full suites, then commit:

  ```powershell
  git add builder_lab/snapshots.py tests/builder_lab_cases/test_snapshots.py
  git commit -m "feat: safely import agent snapshots"
  ```

## Task 4: Implement the in-memory run and event store

**Files:**

- Create: `builder_lab/store.py`
- Create: `tests/builder_lab_cases/test_store.py`

- [ ] Write async tests for unguessable run IDs, append-only strictly increasing event sequences, snapshot isolation, artifact revision monotonicity, replay after a sequence, waiter wake-up, cancellation flags, terminal-state immutability, TTL pruning, maximum-run pruning, and preservation of the last valid artifact after a failure.
- [ ] Run the focused test and observe failure.
- [ ] Implement `RunStore` around `asyncio.Lock` and `asyncio.Condition`. Store bounded event histories and artifacts in memory, make returned values immutable copies, and expose `create`, `snapshot`, `append_event`, `commit_artifact`, `events_after`, `wait_for_events`, `request_cancel`, `mark_terminal`, and `prune`.
- [ ] Rerun focused and full suites, then commit:

  ```powershell
  git add builder_lab/store.py tests/builder_lab_cases/test_store.py
  git commit -m "feat: add builder run event store"
  ```

## Task 5: Assemble the trusted preview document

**Files:**

- Create: `builder_lab/preview.py`
- Create: `tests/builder_lab_cases/test_preview.py`

- [ ] Write tests proving the preview document includes the exact restrictive CSP, only the validated body/CSS, a fixed versioned Kaigo runtime, no generated JavaScript slot, no secrets, escaped metadata, and a render acknowledgement message. Test that the parent iframe contract is `sandbox="allow-scripts"` and never adds `allow-same-origin`.
- [ ] Run the focused test and observe failure.
- [ ] Implement `build_preview_document(artifact)` and `preview_iframe_attributes()`. The fixed inline runtime may toggle launcher/panel, switch sample suggestions, and report `{source:"kaigo-builder-preview", version:1, type:"rendered", revision}`; it must not perform network access.
- [ ] Rerun focused and full suites, then commit:

  ```powershell
  git add builder_lab/preview.py tests/builder_lab_cases/test_preview.py
  git commit -m "feat: add sandboxed widget preview"
  ```

## Task 6: Implement direct staged Gemini generation

**Files:**

- Create: `builder_lab/engines/__init__.py`
- Create: `builder_lab/engines/base.py`
- Create: `builder_lab/prompts.py`
- Create: `builder_lab/engines/gemini_direct.py`
- Create: `tests/builder_lab_cases/test_gemini_direct.py`

- [ ] Write fake-client tests proving the six-stage order, complete previous-artifact handoff, premium anti-generic art-direction prompt, Russian locale, schema-constrained JSON request, model/temperature/top-p propagation, repair issue injection, response parsing, usage extraction including thinking tokens, cancellation propagation, custom API base URL support, and sanitization of quota/model/provider failures.
- [ ] Run the focused test and observe failure.
- [ ] Define a provider-neutral async `BuilderEngine` protocol and `EngineResult` containing the complete candidate, usage delta, provider request ID, and diagnostic summary.
- [ ] Implement stage prompt builders with explicit required regions, safe HTML/CSS contract, visual quality criteria, complete-candidate requirement, and revision/stage invariants. No prompt may request JavaScript or external assets.
- [ ] Implement `GeminiDirectEngine` using `google.genai.Client(...).aio.models.generate_content(...)`, `types.GenerateContentConfig(response_mime_type="application/json", response_json_schema=...)`, and configurable `types.HttpOptions(base_url=...)`. Keep client construction injectable for tests and close owned clients.
- [ ] Parse only the documented response text/usage fields, reject an empty candidate, and map provider exceptions without exposing protected routing details.
- [ ] Rerun focused and full suites, then commit:

  ```powershell
  git add builder_lab/engines builder_lab/prompts.py tests/builder_lab_cases/test_gemini_direct.py
  git commit -m "feat: generate staged widgets with Gemini"
  ```

## Task 7: Implement the Antigravity managed-agent adapter

**Files:**

- Create: `builder_lab/engines/antigravity.py`
- Create: `tests/builder_lab_cases/test_antigravity.py`

- [ ] Write fake Interactions API and fake HTTP download tests for inline starter sources, strict environment network policy, background creation, bounded polling, progress diagnostics, cancellation, environment ID capture/reuse, official Files snapshot URL construction, maximum download size, safe snapshot collection, build-report recording, and precise unavailable/timeout/download/rejected errors.
- [ ] Run the focused test and observe failure.
- [ ] Implement fixed inline `AGENTS.md`, a no-dependency starter validation script, the artifact JSON contract, and a managed-agent instruction that requires the two declared outputs and runs the fixed validator.
- [ ] Implement `AntigravityEngine` with injected SDK and HTTP clients. Use `antigravity-preview-05-2026`, a remote environment, background mode, strict time/size budgets, cooperative cancellation, and `collect_declared_snapshot()` as the only artifact import path.
- [ ] Treat final text and `out/build-report.json` as diagnostics only. Return success only when the independently parsed artifact exists.
- [ ] Rerun focused and full suites, then commit:

  ```powershell
  git add builder_lab/engines/antigravity.py tests/builder_lab_cases/test_antigravity.py
  git commit -m "feat: add Antigravity builder adapter"
  ```

## Task 8: Orchestrate real stages, validation, repair, and cancellation

**Files:**

- Create: `builder_lab/orchestrator.py`
- Create: `tests/builder_lab_cases/test_orchestrator.py`

- [ ] Write fake-engine tests for real event order, direct stage order, preview commits only after successful validation, monotonic revisions, usage accumulation, at least four intermediate commits, bounded repair, repeated-issue fingerprint stopping, preservation of the previous artifact on failure, engine exception mapping, cancellation before/during a stage, Antigravity single-build behavior, retry creation from a failed request, and terminal cleanup.
- [ ] Run the focused test and observe failure.
- [ ] Implement `BuilderOrchestrator` with a task registry, engine registry, request validation, create/start/cancel/retry methods, direct-stage loop, Antigravity path, deterministic validation, two-attempt repair loop, stable events, and aggregate elapsed/usage values.
- [ ] Ensure provider candidates are never committed before validation and the orchestrator never writes production models or calls production widget repositories.
- [ ] Rerun focused and full suites, then commit:

  ```powershell
  git add builder_lab/orchestrator.py tests/builder_lab_cases/test_orchestrator.py
  git commit -m "feat: orchestrate builder generation runs"
  ```

## Task 9: Add the loopback-only aiohttp API, SSE, and laboratory UI

**Files:**

- Create: `builder_lab/ui.py`
- Create: `builder_lab/web.py`
- Create: `tests/builder_lab_cases/test_web.py`

- [ ] Write aiohttp test-client tests for `GET /`, `POST /api/runs`, `GET /api/runs/{id}`, `GET /api/runs/{id}/events`, `POST /api/runs/{id}/cancel`, `POST /api/runs/{id}/retry`, and `GET /api/runs/{id}/preview`. Cover JSON/content types, validation errors, unknown IDs, no-cache headers, security headers, SSE event IDs/replay/heartbeats/terminal close, and preview CSP.
- [ ] Run the focused test and observe failure.
- [ ] Implement `create_builder_lab_app()` with only the dedicated lab routes and injected store/orchestrator. Convert all errors to the stable public schema and sanitize logs.
- [ ] Implement a self-contained two-column page: engine/brief/creativity controls and real event timeline on the left; persistent sandboxed desktop/mobile iframe, revision, validation, usage, elapsed time, and experiment warning on the right. The page must reconnect SSE using the last event ID and update the iframe only on `artifact.committed`.
- [ ] Keep the UI dependency-free and never render provider diagnostics through `innerHTML`.
- [ ] Rerun focused and full suites, then commit:

  ```powershell
  git add builder_lab/ui.py builder_lab/web.py tests/builder_lab_cases/test_web.py
  git commit -m "feat: add builder lab web interface"
  ```

## Task 10: Add the standalone runner, container service, and operator documentation

**Files:**

- Create: `scripts/run_builder_lab.py`
- Create: `scripts/smoke_builder_lab.py`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `README.md`
- Create: `docs/KAIGO_BUILDER_LAB_OPERATIONS.md`
- Create: `tests/builder_lab_cases/test_runner.py`

- [ ] Write runner/config tests proving loopback is the default, non-loopback fails closed, no production `app.server` routes are imported, direct engine is registered only when a key is configured, and Antigravity remains an optional mode.
- [ ] Run the focused test and observe failure.
- [ ] Implement the runner and a non-interactive smoke script that creates a run, consumes the event stream to a terminal state, downloads the preview, and exits nonzero on invalid/failed output.
- [ ] Add a separate Compose service named `builder-lab`, reuse the application image and Gemini-only routing environment, command it to run `scripts/run_builder_lab.py`, bind only `127.0.0.1:8091:8091`, and do not add nginx labels or production dependencies.
- [ ] Document local startup, the SSH local forward, direct/Antigravity controls, expected usage, cancellation, log inspection, credential rotation, and the explicit fact that this lab cannot publish production widgets.
- [ ] Add only non-secret configuration names/defaults to `.env.example` and rerun focused/full suites.
- [ ] Commit:

  ```powershell
  git add scripts/run_builder_lab.py scripts/smoke_builder_lab.py .env.example docker-compose.yml README.md docs/KAIGO_BUILDER_LAB_OPERATIONS.md tests/builder_lab_cases/test_runner.py
  git commit -m "feat: package standalone Gemini builder lab"
  ```

## Task 11: Perform local safety, integration, and regression verification

**Files:**

- Modify only if a verified defect is found in the new lab files.

- [ ] Run the complete new suite from a clean process:

  ```powershell
  & "D:\papka for all\work\kaigo.widgets\.venvs\gemini-builder-lab\Scripts\python.exe" -m unittest discover -s tests -v
  ```

- [ ] Compile all existing and new Python modules:

  ```powershell
  & "D:\papka for all\work\kaigo.widgets\.venvs\gemini-builder-lab\Scripts\python.exe" -m compileall -q app core wrappers builder_lab scripts main.py
  ```

- [ ] Validate dependency consistency inside the isolated environment:

  ```powershell
  & "D:\papka for all\work\kaigo.widgets\.venvs\gemini-builder-lab\Scripts\python.exe" -m pip check
  ```

- [ ] Build the Docker image and render the Compose configuration:

  ```powershell
  docker compose config --quiet
  docker compose build builder-lab
  ```

- [ ] Start the lab locally with fake engines in the HTTP integration test, open the page in a real browser, and capture desktop/mobile screenshots. Confirm the sandbox lacks `allow-same-origin`, the current production app still compiles, and `git diff -- app core wrappers main.py` is empty except pre-existing branch changes.
- [ ] Inspect `git diff --check`, `git status --short`, and the full branch diff. Fix only verified defects through a new failing regression test.
- [ ] Commit any verification fixes separately, then push:

  ```powershell
  git push origin codex/gemini-technical-foundation
  ```

## Task 12: Run live Gemini evidence and deploy the loopback laboratory

**Files:**

- Do not commit secrets or live outputs.
- Modify operator docs only if the observed procedure differs materially from the documented one.

- [ ] On the authorized server, inspect the current checkout, Compose project, protected Gemini routing variables, key availability, free disk, listening ports, and nginx mappings before changing state. Preserve all unrelated server files and containers.
- [ ] Pull the pushed branch into the intended `/root/ai_project` checkout only after confirming it is the same repository and the working tree has no conflicting user changes.
- [ ] Build and start only the `builder-lab` Compose service. Verify it listens on `127.0.0.1:8091` and is absent from public nginx/domain routes.
- [ ] Through an SSH local forward, run the real direct Gemini smoke brief. Require `run.completed`, four or more validated committed revisions, nonzero usage, and a renderable final preview. Record sanitized model, stage, latency, usage, validation, and cost evidence without recording the key.
- [ ] If the project exposes Antigravity, run one bounded comparison. A precise `agent_unavailable` is an acceptable experiment result; a successful result requires the downloaded declared artifact and independent validation.
- [ ] Open the forwarded page in a real browser, inspect desktop/mobile revisions, exercise launcher/suggestions/composer, confirm no preview network requests, and capture screenshots outside committed source.
- [ ] Recheck the existing production health endpoint and one existing public widget. Confirm no production database/widget rows changed and no public site/static paths changed.
- [ ] If any live gate fails, stop only the lab service, preserve diagnostics, and leave production untouched. If all gates pass, keep the lab loopback-only and update the operations evidence in a final commit/push only when it contains no secret or transient identifier.

## Completion gate

The branch is complete only when the automated suite, compile check, isolated `pip check`, Compose validation, browser sandbox inspection, real direct Gemini run, server loopback check, and existing production regression checks all have fresh passing evidence. Antigravity availability is reported independently and never blocks the working direct Gemini path unless the user explicitly promotes it to a required engine.
