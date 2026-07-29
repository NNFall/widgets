# Truthful Public Publication Copy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove public promises of project refinement that the MVP does not provide while preserving the landing page composition and accurately presenting preview, dialogue checks, publication, and connection.

**Architecture:** Keep all existing page sections and the two-column Studio showcase grid. Replace the interactive refinement simulation with a static three-step checklist and keep the existing browser preview; update copy-only claims in marketing and authentication components without changing application APIs, backend behavior, or project recovery.

**Tech Stack:** React 19, TypeScript, Testing Library/Vitest, Playwright, CSS.

---

### Task 1: Lock the truthful public contract in component tests

**Files:**
- Modify: `frontend/src/landing/LandingPage.test.tsx`
- Modify: `frontend/src/auth/AuthGate.test.tsx`

- [ ] **Step 1: Write failing landing assertions**

Add assertions that the rendered landing page contains `Платите только за публикацию и подключение`, the three checklist labels `Предпросмотр`, `Проверка диалога`, and `Публикация и подключение`, and contains no refinement textarea/button, `История версий`, `Можно дорабатывать`, or FAQ promise that answers can be changed.

- [ ] **Step 2: Write a failing AuthGate assertion**

Assert that the unauthenticated gate says `Платить нужно только за публикацию и подключение готового виджета` and contains no text matching `/дорабат/i`.

- [ ] **Step 3: Verify RED**

Run `npx vitest run src/landing/LandingPage.test.tsx src/auth/AuthGate.test.tsx` from `frontend/`. Expected: failures identifying the current refinement promises and interactive demo controls.

### Task 2: Make every public promise match the MVP

**Files:**
- Modify: `frontend/src/landing/FreeResultSection.tsx`
- Modify: `frontend/src/auth/AuthGate.tsx`
- Modify: `frontend/src/landing/StudioSection.tsx`
- Modify: `frontend/src/landing/FaqSection.tsx`
- Modify: `frontend/src/landing/CapabilitiesSection.tsx`
- Modify: `frontend/src/landing/HowItWorksSection.tsx`
- Modify: `frontend/src/landing/HeroOrbitScene.tsx`

- [ ] **Step 1: Update payment and authentication copy**

Replace refinement claims with publication/connection language. Keep the existing free-result, sign-in, and payment boundaries unchanged.

- [ ] **Step 2: Replace the Studio demo interaction**

Remove React state, the refinement textarea, apply button, version history, and revision animation. Render a static left rail with three visual checklist items: preview, dialogue check, and publication/connection. Keep the existing `studio-demo`, sidebar, workspace, toolbar, and `BrowserMockup` structure so the composition remains stable.

- [ ] **Step 3: Update analogous marketing claims**

Describe checking rather than changing behavior in FAQ, capabilities, How It Works, and hero orbit status. Do not alter backend/project-recovery code or legacy builder refinement behavior.

- [ ] **Step 4: Verify GREEN**

Run `npx vitest run src/landing/LandingPage.test.tsx src/auth/AuthGate.test.tsx` from `frontend/`. Expected: all focused tests pass.

### Task 3: Verify layout and the full frontend

**Files:**
- Modify only if visual comparison requires it: `frontend/src/styles.css`
- Modify only after an intentional accepted visual change: `frontend/e2e/landing.spec.ts-snapshots/*.png`
- Modify: `docs/product-journal/2026-07.md`

- [ ] **Step 1: Run focused Playwright**

Run the desktop landing visual/interaction tests and the mobile landing visual test. Inspect the generated before/after screenshots, confirm the Studio demo retains its two-column hierarchy, and update snapshots only for intentional content differences.

- [ ] **Step 2: Run the full frontend verification**

Run `npm test -- --run`, `npm run typecheck`, and `npm run lint` from `frontend/`. Expected: exit code 0 for every command.

- [ ] **Step 3: Record the product limitation honestly**

Append a short Russian entry to `docs/product-journal/2026-07.md` stating that public pages now promise only preview/dialogue checks/publication/connection and no longer imitate unavailable project refinement.

- [ ] **Step 4: Check scope**

Run `git diff --check` on the touched frontend and journal files and inspect the scoped diff. Do not commit.
