# Contact, feedback, and legal pages implementation plan

> Execute sequentially with fresh Luna implementation agents. After each implementation task, run specification review and code-quality review before moving on. Do not deploy production.

**Goal:** Add truthful contact/feedback paths to the landing page and Studio, plus complete preview-ready legal information architecture and backend handoff documentation.

**Architecture:** Shared contact configuration and a reusable mail composer feed a landing contact section and a Studio contact drawer. A shared legal layout renders four exact App routes. Narrow nginx and archive-transformer routes expose those pages without changing backend ownership.

**Tech stack:** React 19, TypeScript, Vite, Vitest/Testing Library, Framer Motion, CSS, Playwright, Python deployment tests, nginx package templates, Node archive-transform tests.

---

## Task 1: Shared contact contract, landing section, footer, and legal pages

**Files:**

- Create: `frontend/src/shared/contact.ts`
- Create: `frontend/src/shared/FeedbackComposer.tsx`
- Create: `frontend/src/landing/ContactSection.tsx`
- Create: `frontend/src/landing/SiteFooter.tsx`
- Create: `frontend/src/legal/LegalPage.tsx`
- Create: `frontend/src/legal/legalDocuments.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/landing/LandingPage.tsx`
- Modify: `frontend/src/landing/FinalCtaSection.tsx`
- Modify: `frontend/src/landing/FaqSection.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/mobile/foundations.css`
- Test: focused shared/landing/legal tests, `App.test.tsx`, `LandingPage.test.tsx`, motion/accessibility contracts

1. Write failing tests for encoded mail subjects/bodies, message and separate-consent gating, exact legal routes, linked footer documents, operator placeholders, and the FAQ contact action.
2. Run the focused tests and record the expected failures.
3. Implement the smallest shared contact configuration and components that satisfy the truthful mail-only contract.
4. Add the landing contact band and reusable footer without nested-card visual noise.
5. Add the four legal documents and shared legal layout with explicit preview placeholders.
6. Add mobile-first CSS, keyboard focus, 44 px targets, and readable line lengths.
7. Run focused tests, typecheck, lint, and commit.

## Task 2: Studio feedback drawer and authentication support

**Files:**

- Create: `frontend/src/studio/StudioContactPanel.tsx`
- Modify: `frontend/src/studio/StudioProjectWorkbench.tsx`
- Modify: `frontend/src/auth/AuthGate.tsx`
- Modify: `frontend/src/styles.css`
- Test: `StudioContactPanel.test.tsx`, `SaasStudioFlow.test.tsx`, `AuthGate.test.tsx`, Studio accessibility contracts

1. Write failing tests for Account → Contact, topic mail links, consent gating, Escape/focus restoration, inert Telegram state, and support access during authentication failure.
2. Run the focused tests and record RED.
3. Add the Contact drawer to the existing state machine and an Account launcher; do not add another workbench-header icon.
4. Add a plain support path to the authentication error state.
5. Verify desktop/mobile styling, focused tests, typecheck, lint, and commit.

## Task 3: Exact deployment and archive-preview routing

**Files:**

- Modify: `deploy/nginx/kaigo-marketing-site.conf`
- Modify: `tests/deployment_cases/test_marketing_site_package.py`
- Modify: `scripts/frontend_history_transform.mjs`
- Modify: `scripts/frontend_history_transform.test.mjs`
- Modify: `deploy/frontend-history/versions.json`
- Create: `docs/CONTACT_AND_LEGAL_HANDOFF.md`

1. Write failing tests for exact `/privacy[/]`, `/personal-data-consent[/]`, `/terms[/]`, and `/offer[/]` SPA routes plus archive wrappers.
2. Run focused Node and Python tests and record RED.
3. Add narrow nginx blocks without changing the legacy catch-all.
4. Generalize archive wrappers so legal paths use their real pathname inside v11 while preserving API isolation.
5. Write the backend integration, compliance-validation, and production-readiness checklist with official-source links.
6. Run focused Node/Python tests and commit.

## Task 4: Integrated browser verification

**Files:**

- Modify: `frontend/e2e/landing.spec.ts`
- Modify: `frontend/e2e/studio.spec.ts`

1. Add browser contracts for landing contact behavior, legal navigation, Studio contact drawer, keyboard interaction, mobile touch targets, and horizontal overflow.
2. Run focused tests at desktop and 320/360/390/430 mobile widths.
3. Inspect screenshots for hierarchy, line wrapping, footer density, drawer scrolling, and legal-page readability.
4. Fix any Critical/Important issue through a fresh Luna implementation task and re-run.

## Task 5: Final verification, review, and isolated preview

1. Run full Vitest, typecheck, lint, production build, deployment tests, archive-transformer tests, and the relevant Playwright suites.
2. Run independent specification and code-quality reviews; resolve all Critical/Important findings.
3. Confirm only intended files changed and no secrets/requisites were invented.
4. Commit, push `codex/product-ui`, and record the commit SHA for backend integration.
5. Build and publish isolated `/frontend/v11/` using the established preview workflow only.
6. Smoke-check landing, Studio, all four legal routes, assets, API isolation, and noindex behavior.
7. Confirm production checkout/services were not changed.

