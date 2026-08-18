# OAuth-to-Studio automatic start Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send a project claimed from an anonymous draft directly into the existing express generation flow after OAuth, without rendering the duplicate `StudioComposer` stage.

**Architecture:** OAuth callback and frontend draft-claim redirect carry a narrow `autostart=1` marker. `StudioPage` consumes the marker once, waits for normal project hydration, and calls the existing `useBuilderRun.createRun` API with the hydrated project URL and brief. Manual `/studio?project=<id>` navigation remains unchanged, and an active run always wins over autostart.

**Tech Stack:** aiohttp auth routes, React/TypeScript, Vitest + Testing Library, pytest/aiohttp route tests, Playwright smoke.

---

## Task 1: Lock the redirect contract with tests

- [x] Update `frontend/src/auth/AuthGate.test.tsx` to expect `project=<id>&autostart=1` after a bound draft claim.
- [x] Add/adjust auth callback assertions in `tests/saas_cases/test_auth_routes.py`: a callback that claims a project includes `autostart=1`; a callback without a project remains `/studio` without the marker.
- [x] Run the focused frontend/backend tests and record the expected RED failures before implementation.

## Task 2: Add Studio autostart behavior tests

- [x] Add a SaaS flow test for `/studio?project=<id>&autostart=1` with a hydrated project and no active run: exactly one run POST, no `StudioComposer`, and a compact progress state.
- [x] Add a no-duplicate test for an already active run and retain the existing manual `/studio?project=<id>` composer behavior.
- [x] Run the focused Vitest suite and record RED failures.

## Task 3: Implement the redirect marker

- [x] Update `AuthGate.tsx` to preserve `project` and add exact `autostart=1` when the frontend claims a draft.
- [x] Update `app/auth/routes.py` to add the marker only when the OAuth callback returns a claimed project; leave non-project callbacks at `/studio`.

## Task 4: Implement one-shot Studio autostart

- [x] Parse only `autostart=1` in `StudioPage.tsx`.
- [x] After project hydration and only when there is no active run, call the existing `controller.createRun` with the project’s source URL/brief and existing express settings.
- [x] Remove the marker before launching, guard the attempt by project id, and provide a compact loading/error/retry state instead of `StudioComposer` for this marked flow.
- [x] Keep active-run hydration and manual project navigation unchanged.

## Task 5: Verify and hand off

- [x] Run focused AuthGate/Studio Vitest tests, backend auth-route tests, typecheck, lint, and diff checks.
- [ ] Run the relevant browser smoke if the local app is available; report any environment-only limitation separately.
- [ ] Review the diff, commit the implementation, and summarize the exact changed paths and verification evidence.
