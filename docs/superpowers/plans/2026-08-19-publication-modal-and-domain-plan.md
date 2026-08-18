# Publication modal and source-domain publication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Make the publication flow readable in a near-fullscreen modal and make a valid project source domain publish successfully without manual domain entry.

**Architecture:** Preserve `StudioDrawer` focus/Escape/backdrop behavior and add a presentation variant used only by publication; leave projects, versions, and account as side drawers. Pass the project source URL into `UpgradeGate`, initialize the exact HTTPS origin, and make the versioned publication route derive that origin when `allowed_domains` is omitted. Keep all prices and plan details server-driven.

**Tech Stack:** React 18, TypeScript, Vitest/Testing Library, Playwright, aiohttp, pytest, SQLAlchemy, existing CSS tokens.

---

### Task 1: Lock the versioned publication contract

**Files:**
- Modify: `tests/saas_cases/test_publication.py` near the versioned route acceptance test around line 2350.
- Modify: `app/publication/routes.py:462-478`.

- [x] **Step 1: Write the failing test**

Add a request after the first versioned publish body is constructed. Omit `allowed_domains`, keep `project_version_id` and `expected_active_release_id`, and assert HTTP 201 plus `allowed_domains == ["https://example.com"]`, using the seeded project source URL. The test must use the existing authenticated test client and CSRF header.

- [x] **Step 2: Run the test to verify it fails**

Run:

```powershell
python -m pytest tests/saas_cases/test_publication.py -k "versioned and omitted" -q
```

Expected: HTTP 400 from the current `required_keys` set because `allowed_domains` is missing.

- [x] **Step 3: Write the minimal implementation**

In the `_versions_enabled` branch of `publish_project`, change only `required_keys` from `{project_version_id, expected_active_release_id, allowed_domains}` to `{project_version_id, expected_active_release_id}`. Keep `allowed_domains` in `allowed_keys` and continue passing `_allowed_domains(payload)` so explicit arrays remain validated and omission reaches `PublicationService._domains(...)=source_origin`.

- [x] **Step 4: Run the focused backend test**

Run:

```powershell
python -m pytest tests/saas_cases/test_publication.py -k "versioned and omitted" -q
```

Expected: 1 passed.

- [x] **Step 5: Commit**

```powershell
git add tests/saas_cases/test_publication.py app/publication/routes.py
git commit -m "fix: derive source domain for versioned publication"
```

### Task 2: Make the project source origin visible in the publication gate

**Files:**
- Modify: `frontend/src/studio/UpgradeGate.tsx:51-115,130-180,490-565`.
- Modify: `frontend/src/studio/StudioProjectWorkbench.tsx:374-398`.
- Modify: `frontend/src/studio/UpgradeGate.test.tsx` with a source-origin initialization case and an actionable validation-error case.

- [x] **Step 1: Write the failing tests**

Add a render with `sourceUrl="https://Example.COM/products/widget?campaign=private"` and `projectId`, mock `getProjectPublication` to return `{ publication: null }`, and assert the publication textarea eventually has value `https://example.com`. Add a publish rejection mock with `BuilderApiError("allowed domain must use HTTPS", {status: 422, code: "publication_invalid", raw: ...})` and assert the exact safe message is rendered instead of the generic “Проверьте домены”.

- [x] **Step 2: Run the tests to verify they fail**

```powershell
Set-Location frontend
npx.cmd vitest run src/studio/UpgradeGate.test.tsx -t "source origin|validation error" --reporter=verbose
```

Expected: the textarea is empty and the generic error text is shown.

- [x] **Step 3: Write the minimal implementation**

Add optional `sourceUrl?: string` to `UpgradeGateProps`, a helper that returns `new URL(sourceUrl).origin` only for HTTPS URLs, and initialize `allowedDomains` from that helper. When authoritative publication state is `null`, restore the same default origin. In `publish`’s non-conflict catch, if the caught error is `BuilderApiError` with a non-empty `message` and a 4xx publication validation code, render `Не удалось опубликовать: ${caught.message}.` otherwise retain the generic retry message. Pass `sourceUrl` from `StudioProjectWorkbench`.

- [x] **Step 4: Run focused frontend tests**

```powershell
Set-Location frontend
npx.cmd vitest run src/studio/UpgradeGate.test.tsx --reporter=dot
```

Expected: the full UpgradeGate file passes.

- [x] **Step 5: Commit**

```powershell
git add frontend/src/studio/UpgradeGate.tsx frontend/src/studio/UpgradeGate.test.tsx frontend/src/studio/StudioProjectWorkbench.tsx
git commit -m "fix: default publication access to project origin"
```

### Task 3: Add the near-fullscreen publication presentation

**Files:**
- Modify: `frontend/src/studio/StudioDrawer.tsx` to accept `variant?: 'drawer' | 'modal'` and add the class only for the modal variant.
- Modify: `frontend/src/studio/StudioProjectWorkbench.tsx:374-398` to use `variant="modal"` for publication and keep other drawers unchanged.
- Modify: `frontend/src/styles.css` near the existing `.studio-drawer` rules.
- Modify: `frontend/src/studio/SaasStudioFlow.test.tsx` to assert the modal class and unchanged side-drawer class.

- [x] **Step 1: Write the failing tests**

In the existing publication-flow test, assert the publication dialog has class `studio-drawer--modal` and its panel has class `studio-drawer__panel--modal`. In the account drawer assertion, assert `studio-drawer--modal` is absent. Keep the existing accessible dialog and launch-step assertions.

- [x] **Step 2: Run the tests to verify they fail**

```powershell
Set-Location frontend
npx.cmd vitest run src/studio/SaasStudioFlow.test.tsx -t "publication flow|publication dialog" --reporter=verbose
```

Expected: class assertions fail because publication currently uses the default drawer presentation.

- [x] **Step 3: Write the minimal implementation**

Add the variant prop with default `drawer`; compose `studio-drawer studio-drawer--modal` and `studio-drawer__panel studio-drawer__panel--modal` only for the modal. Render the publication `StudioDrawer` with `variant="modal"`. Add desktop CSS that centers a `min(1240px, 100%)` panel with `max-height: calc(100dvh - 48px)`, rounded corners, readable body padding, and a scrollable body. At `max-width: 760px`, make the modal full-bleed and retain the existing mobile body scroll. Do not alter default drawer width.

- [x] **Step 4: Run focused frontend tests**

```powershell
Set-Location frontend
npx.cmd vitest run src/studio/SaasStudioFlow.test.tsx -t "publication flow|publication dialog" --reporter=dot
```

Expected: 1 passed and account/versions drawer assertions remain green.

- [x] **Step 5: Commit**

```powershell
git add frontend/src/studio/StudioDrawer.tsx frontend/src/studio/StudioProjectWorkbench.tsx frontend/src/styles.css frontend/src/studio/SaasStudioFlow.test.tsx
git commit -m "feat: present publication flow in a full modal"
```

### Task 4: Clarify access and protect plan-card readability

**Files:**
- Modify: `frontend/src/studio/PublicationOfferDialog.tsx` around the header and founder section.
- Modify: `frontend/src/styles.css` around `.publication-offer__*` rules.
- Modify: `frontend/src/studio/PublicationOfferDialog.test.tsx`.

- [x] **Step 1: Write the failing test**

Assert the offer dialog contains an accessible section or heading named `Доступ к публикации`, explains “Founder-пилот или платный тариф”, and exposes the founder CTA and all three prices. Assert each plan card has a minimum-width class hook and the dialog has the modal-flow marker class.

- [x] **Step 2: Run the test to verify it fails**

```powershell
Set-Location frontend
npx.cmd vitest run src/studio/PublicationOfferDialog.test.tsx -t "доступ|план" --reporter=verbose
```

Expected: the access explanation and new marker are absent.

- [x] **Step 3: Write the minimal implementation**

Add a short server-neutral access introduction below the header: `ДОСТУП К ПУБЛИКАЦИИ`, heading `Выберите, как открыть виджет на сайте`, and copy explaining that eligible owners can activate Founder while others choose a paid plan. Keep all amounts/periods/tokens sourced from `offer`. Add `min-width: 0`, `overflow-wrap: anywhere`, and a desktop `grid-template-columns: repeat(3, minmax(220px, 1fr))`; keep the existing mobile single-column breakpoint. Add a stable `data-publication-flow="modal"` hook on the dialog section.

- [x] **Step 4: Run focused frontend tests**

```powershell
Set-Location frontend
npx.cmd vitest run src/studio/PublicationOfferDialog.test.tsx --reporter=dot
```

Expected: the full offer-dialog file passes.

- [x] **Step 5: Commit**

```powershell
git add frontend/src/studio/PublicationOfferDialog.tsx frontend/src/studio/PublicationOfferDialog.test.tsx frontend/src/styles.css
git commit -m "fix: clarify publication access offers"
```

### Task 5: Verify desktop/mobile behavior and complete the handoff

**Files:**
- Modify: `frontend/e2e/studio.spec.ts` only if existing selectors or assertions need the modal class.

- [x] **Step 1: Update the browser regression**

At the existing publication E2E, set viewport `1920x1080` before opening publication, assert `.studio-drawer--modal` panel width is at least 900 CSS pixels, assert no horizontal overflow, and verify the offer path shows `14 дней бесплатно`, `500 ₽`, `2 000 ₽`, and `5 000 ₽`. Add a mobile test at `390x844` asserting the modal remains within viewport and plan cards stack without horizontal overflow.

- [x] **Step 2: Run the browser regression**

```powershell
Set-Location frontend
npx.cmd playwright test e2e/studio.spec.ts --grep "publication|tariff" --project=chromium
```

Expected: desktop and mobile publication tests pass; no console/page errors from the new modal.

- [x] **Step 3: Run the full scoped verification**

```powershell
python -m pytest tests/saas_cases/test_publication.py -q
Set-Location frontend
npm run typecheck
npm run lint -- --no-cache
npx.cmd vitest run src/studio/UpgradeGate.test.tsx src/studio/PublicationOfferDialog.test.tsx src/studio/SaasStudioFlow.test.tsx --reporter=dot
npx.cmd playwright test e2e/studio.spec.ts --grep "publication|tariff" --project=chromium
```

Expected: all listed commands exit 0; any pre-existing unrelated warning is reported separately rather than hidden.

- [x] **Step 4: Inspect the final diff**

```powershell
git diff --check
git status --short
git log -5 --oneline
```

Confirm only the design doc, plan, publication backend, modal UI, and their tests changed. Do not deploy production in this task.
