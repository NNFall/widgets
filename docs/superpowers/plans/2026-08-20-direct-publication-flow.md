# Direct Publication Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the nested access-to-publication sequence with one continuous modal that goes directly from access selection to automatic publication and installation handoff, while fixing the production frontend/backend request-contract mismatch.

**Architecture:** `StudioDrawer` remains the only dialog and focus owner. `UpgradeGate` orchestrates billing recovery, access activation, one automatic first publication, retry, and existing-publication updates; presentation is split into an embedded access view and an installation view. First publication omits `allowed_domains` and therefore requires the compatible backend route already present in this branch; updates keep the restored exact allowlist.

**Tech Stack:** React 19, TypeScript, Vitest/Testing Library, Playwright, aiohttp, SQLAlchemy/PostgreSQL, CSS, immutable Docker and static releases.

---

## File map

- Rename `frontend/src/studio/PublicationOfferDialog.tsx` to `frontend/src/studio/PublicationAccessView.tsx`: render the large inline Founder and paid-plan offer without a backdrop or nested dialog.
- Rename `frontend/src/studio/PublicationOfferDialog.test.tsx` to `frontend/src/studio/PublicationAccessView.test.tsx`: lock the offer copy, server-driven terms, accessibility, and embedded presentation.
- Create `frontend/src/studio/PublicationInstallView.tsx`: successful publication and code/link handoff.
- Create `frontend/src/studio/PublicationInstallView.test.tsx`: copy, fallback, guide, and developer details.
- Modify `frontend/src/studio/UpgradeGate.tsx`: continuous flow orchestration, one automatic first publish, retry behavior, and feedback visibility.
- Modify `frontend/src/studio/UpgradeGate.test.tsx`: RED-to-GREEN integrated flow contracts.
- Modify `frontend/src/studio/SaasStudioFlow.test.tsx`: one accessible publication dialog and direct offer/install states.
- Modify `frontend/e2e/studio.spec.ts`: desktop/mobile customer flow and request body.
- Modify `frontend/src/styles.css`: wide desktop offer, mobile stacking, progress/error and installation layouts.
- Verify existing `app/publication/routes.py` and `tests/saas_cases/test_publication.py`: compatible optional-domain backend contract.
- Modify `docs/SAAS_PRODUCTION_RUNBOOK.md` and `tests/deployment_cases/test_saas_production_contract.py`: require the app image and static site to be built from the same release SHA when the publication contract changes.
- Modify the editorial contour required by `AGENTS.md` after live verification.

### Task 1: Lock the continuous customer-flow contract

**Files:**
- Modify: `frontend/src/studio/UpgradeGate.test.tsx`
- Modify: `frontend/src/studio/SaasStudioFlow.test.tsx`
- Modify: `frontend/e2e/studio.spec.ts`

- [ ] **Step 1: Add a RED test for direct access presentation**

Render an eligible unauthenticated-to-billing `UpgradeGate` with `getBillingSubscription` returning `null`, `getPendingBillingPayment` returning `null`, and `getBillingOffer` returning the standard Founder offer. Require the offer to appear without clicking the removed intermediate action:

```ts
expect(await screen.findByRole('heading', {
  name: '14 дней полностью бесплатно',
})).toBeVisible();
expect(screen.queryByRole('button', {
  name: 'Выбрать условия публикации',
})).not.toBeInTheDocument();
expect(screen.getAllByRole('dialog')).toHaveLength(0);
```

The component test renders only `UpgradeGate`, so the access view itself must not create a dialog. `SaasStudioFlow.test.tsx` must assert exactly one `role="dialog"`, owned by `StudioDrawer`.

- [ ] **Step 2: Add a RED Founder success test**

Change the current Founder flow assertion to click only:

```ts
fireEvent.click(await screen.findByRole('button', {
  name: 'Активировать бесплатно и продолжить',
}));
```

Require `claimFounderAccess`, then `publishProject` with the exact body:

```ts
{
  project_version_id: 'version-4',
  expected_active_release_id: null,
}
```

The UI must finish at `Виджет опубликован` with `Скопировать код установки`; it must never render `Всё готово к публикации` or a second `Опубликовать и получить код` action.

- [ ] **Step 3: Add a RED partial-success test**

Resolve Founder claim and reject publish with `BuilderApiError` status 400 and code `invalid_body`. Assert:

```ts
expect(await screen.findByRole('heading', {
  name: 'Доступ подключён, публикация не завершена',
})).toBeVisible();
expect(screen.getByRole('button', { name: 'Повторить публикацию' })).toBeEnabled();
expect(screen.queryByText('Всё готово к публикации')).not.toBeInTheDocument();
expect(screen.queryByRole('dialog', {
  name: 'Расскажите, как прошёл пилот',
})).not.toBeInTheDocument();
```

Click retry and resolve the second publish call; require the installation view.

- [ ] **Step 4: Add a RED active-subscription test**

When billing recovery returns an active subscription and publication recovery returns `null`, mounting `UpgradeGate` inside the explicitly opened publication drawer must issue exactly one automatic first publish. A re-render and a failed attempt must not create an automatic retry loop. Existing publications must restore directly and must not auto-update to another selected version.

- [ ] **Step 5: Add the browser request contract before production changes**

At 1920×1080 and 390×844, open Publication once and require the access offer immediately. Intercept Founder claim and publish; verify one publish request omits `allowed_domains`. After success require one dialog, visible copy actions, a visible `/install` link, no horizontal document overflow, and no nested modal/backdrop.

- [ ] **Step 6: Run RED**

```powershell
Set-Location frontend
npx.cmd vitest run src/studio/UpgradeGate.test.tsx src/studio/SaasStudioFlow.test.tsx --reporter=verbose
```

Expected: failures for the intermediate action, nested dialog, old Founder CTA, intermediate active screen, feedback availability, and missing automatic active-subscription publish.

- [ ] **Step 7: Commit only RED tests**

```powershell
git add frontend/src/studio/UpgradeGate.test.tsx frontend/src/studio/SaasStudioFlow.test.tsx frontend/e2e/studio.spec.ts
git commit -m "test: define direct publication flow"
```

### Task 2: Convert the nested offer dialog into an embedded access view

**Files:**
- Create: `frontend/src/studio/PublicationAccessView.tsx`
- Create: `frontend/src/studio/PublicationAccessView.test.tsx`
- Delete: `frontend/src/studio/PublicationOfferDialog.tsx`
- Delete: `frontend/src/studio/PublicationOfferDialog.test.tsx`
- Modify: `frontend/src/studio/UpgradeGate.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Rename the component and remove dialog ownership**

The new props are:

```ts
interface PublicationAccessViewProps {
  offer: BillingOffer;
  busy: boolean;
  loading: boolean;
  initialIntroConsent?: boolean;
  error: string | null;
  onFounder: () => void;
  onCheckout: (planCode: string, autoRenew: boolean) => void;
}
```

Remove `open`, `onClose`, backdrop click handling, `role="dialog"`,
`aria-modal`, and the duplicate close button. Render one semantic section with
`aria-labelledby="publication-access-title"`.

- [ ] **Step 2: Use explicit Founder copy**

Keep the period server-driven and render the current offer as:

```tsx
<span>FOUNDER-ПИЛОТ · ДЛЯ ПЕРВЫХ КЛИЕНТОВ</span>
<h2 id="publication-access-title">
  {periodLabel} полностью бесплатно
</h2>
<p>
  Мы бесплатно откроем публикацию без карты и автосписаний. Взамен попросим
  честно рассказать, что удобно, чего не хватает и что стоит доработать.
</p>
```

The CTA is exactly `Активировать бесплатно и продолжить`. Keep remaining
places, token allowance, no-payment statement, and the paid alternative.

- [ ] **Step 3: Make the paid alternative subordinate but complete**

Show all server plans below the Founder section under `Или сразу выбрать
платный тариф`. Preserve amounts, periods, token counts, optional intro renewal
consent, and the exact renewal chain. Do not hard-code product values.

- [ ] **Step 4: Implement responsive layout**

Desktop uses a dominant Founder region followed by a three-column paid-plan
grid. Mobile uses one column. Body copy is at most 70ch; buttons and links are
at least 44px; all content fits without horizontal overflow. Remove the nested
modal CSS selectors rather than leaving dead styles.

- [ ] **Step 5: Run focused GREEN and commit**

```powershell
Set-Location frontend
npx.cmd vitest run src/studio/PublicationAccessView.test.tsx src/studio/UpgradeGate.test.tsx --reporter=dot
Set-Location ..
git add frontend/src/studio/PublicationAccessView.tsx frontend/src/studio/PublicationAccessView.test.tsx frontend/src/studio/PublicationOfferDialog.tsx frontend/src/studio/PublicationOfferDialog.test.tsx frontend/src/studio/UpgradeGate.tsx frontend/src/styles.css
git commit -m "feat: present publication access inline"
```

### Task 3: Implement deterministic activation-to-publication orchestration

**Files:**
- Modify: `frontend/src/studio/UpgradeGate.tsx`
- Modify: `frontend/src/studio/UpgradeGate.test.tsx`

- [ ] **Step 1: Replace modal flags with a flow phase**

Remove `offerOpen` and `publishAfterActivation`. Add:

```ts
type PublicationPhase =
  | 'checking'
  | 'access'
  | 'activating'
  | 'publishing'
  | 'publish_error'
  | 'install';
```

Derive the visible phase from authoritative subscription/publication state,
but store transitional and error phases explicitly. Use a ref keyed by
`projectId` to ensure the active-subscription automatic first publish runs once
per mount and never loops after failure.

When billing and pending-payment recovery finish without active access, load
`getBillingOffer(projectId)` automatically and show `PublicationAccessView`.
Keep a visible retry action when offer loading fails; do not restore the removed
intermediate «Выбрать условия публикации» button.

- [ ] **Step 2: Make `publish` return a result**

Refactor the existing callback to return `Promise<boolean>`. It must keep
conflict recovery and safe messages, set `publishing`, set `install` on
success, set `publish_error` on failure, and always clear the network pending
flag. It must not throw user-visible backend text.

- [ ] **Step 3: Keep Founder activation and publication in one awaited action**

`claimFounder` must keep the access view mounted, set `activating`, await
`claimFounderAccess`, update the subscription, and then await `publish()`.
There is no `setOfferOpen(false)` and no effect-mediated background handoff.

- [ ] **Step 4: Apply the same continuation after paid checkout**

When polling confirms a succeeded payment and an active subscription, invoke
the same one-time first-publication continuation. Pending checkout remains on
the access surface with its safe payment link.

- [ ] **Step 5: Auto-publish an already active, unpublished project once**

After billing and publication recovery both complete, an active subscription
with `publication === null` calls `publish()` exactly once. A restored
publication goes directly to `install`. An existing publication is never
automatically updated to another version.

- [ ] **Step 6: Make support context truthful**

Founder feedback is available only when `publication !== null`. Before that,
`SupportDialog` receives `founder={false}` and `createCustomerContact` uses
`kind: 'support'`. The error action may say `Нужна помощь? Связаться с Kaigo`,
but it must not open `Расскажите, как прошёл пилот`.

- [ ] **Step 7: Run focused GREEN and commit**

```powershell
Set-Location frontend
npx.cmd vitest run src/studio/UpgradeGate.test.tsx src/studio/SaasStudioFlow.test.tsx --reporter=dot
Set-Location ..
git add frontend/src/studio/UpgradeGate.tsx frontend/src/studio/UpgradeGate.test.tsx frontend/src/studio/SaasStudioFlow.test.tsx
git commit -m "feat: continue access directly to publication"
```

### Task 4: Extract the installation handoff and error/progress surfaces

**Files:**
- Create: `frontend/src/studio/PublicationInstallView.tsx`
- Create: `frontend/src/studio/PublicationInstallView.test.tsx`
- Modify: `frontend/src/studio/UpgradeGate.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Write the install-view API**

```ts
interface PublicationInstallViewProps {
  publication: PublicationRelease;
  versionOrdinal?: number;
  subscription: BillingSubscription | null;
  copiedTarget: 'code' | 'link' | null;
  copyError: boolean;
  onCopyCode: () => void;
  onCopyLink: () => void;
}
```

Render the visible embed snippet, loader URL, primary/secondary copy buttons,
and `/install`. Keep release metadata inside `details`.

- [ ] **Step 2: Add a stable progress surface**

During activation/publication render one `role="status"` surface with the
three named stages. It must retain a fixed minimum block size to prevent the
dialog from jumping. Only the active step uses the accent; completed steps use
the success color.

- [ ] **Step 3: Add the partial-success error surface**

Render `Доступ подключён, публикация не завершена`, the safe error text,
`Повторить публикацию`, and a secondary technical-support link/button. Do not
render Founder feedback copy.

- [ ] **Step 4: Verify clipboard fallbacks**

Success states distinguish copied code from copied link. Rejection or missing
Clipboard API exposes both selectable literal values. `/install` is a visible
underlined link with a 3px focus ring.

- [ ] **Step 5: Run tests and commit**

```powershell
Set-Location frontend
npx.cmd vitest run src/studio/PublicationInstallView.test.tsx src/studio/UpgradeGate.test.tsx --reporter=dot
Set-Location ..
git add frontend/src/studio/PublicationInstallView.tsx frontend/src/studio/PublicationInstallView.test.tsx frontend/src/studio/UpgradeGate.tsx frontend/src/styles.css
git commit -m "feat: complete publication with install handoff"
```

### Task 5: Verify and guard the backend/frontend release contract

**Files:**
- Existing: `app/publication/routes.py`
- Existing: `tests/saas_cases/test_publication.py`
- Modify: `docs/SAAS_PRODUCTION_RUNBOOK.md`
- Modify: `tests/deployment_cases/test_saas_production_contract.py`

- [ ] **Step 1: Re-run the exact backend regression**

```powershell
python -m pytest tests/saas_cases/test_publication.py -k "versioned_publish_omitted_domains_uses_project_source_origin" -q
```

Expected: 1 passed. Confirm `allowed_domains` remains in `allowed_keys` but not
in `required_keys` for the versioned route.

- [ ] **Step 2: Add a release runbook contract**

Document and test that the app/migration image and marketing archive are built
from the same full release SHA. The release gate must fail if the live app
release identity differs from the static release being activated when their
publication request contract changed. Do not print environment secrets while
checking the identity.

- [ ] **Step 3: Run backend and deployment tests**

```powershell
python -m pytest tests/saas_cases/test_publication.py -q
python -m pytest tests/deployment_cases/test_saas_production_contract.py -q
```

- [ ] **Step 4: Commit the release guard**

```powershell
git add docs/SAAS_PRODUCTION_RUNBOOK.md tests/deployment_cases/test_saas_production_contract.py
git commit -m "docs: gate publication contract releases"
```

### Task 6: Complete local verification and independent review

**Files:**
- Modify only validated files from review findings.

- [ ] **Step 1: Run full frontend gates**

```powershell
Set-Location frontend
npx.cmd vitest run --reporter=dot
npm.cmd run typecheck
npm.cmd run lint -- --no-cache
npm.cmd run build
```

- [ ] **Step 2: Run focused browser tests**

```powershell
npx.cmd playwright test e2e/studio.spec.ts --project=desktop-1920 --project=mobile-390 --grep "publication"
```

Require direct offer, no nested dialog, stable progress/error/install states,
no horizontal overflow, 44px targets, and visible links.

- [ ] **Step 3: Continue the same Anti-Gravity conversation for review**

Use job `a21e7b3f-7a5c-41e5-a7ea-9c2f1cbcf195` with a bounded read-only review
request covering orchestration loops, support context, request compatibility,
accessibility, and 1920/390 layout. Independently inspect its result and rerun
affected tests.

- [ ] **Step 4: Run a separate code review**

Review the frozen diff for P0-P2 regressions. Fix only confirmed findings,
then repeat the relevant focused and full gates.

- [ ] **Step 5: Check the final tree**

```powershell
git diff --check
git status --short --branch
```

### Task 7: Release together and verify production

**Files:**
- Modify: `docs/product-journal/2026-08.md`
- Create/update: `docs/telegram/release-packets/2026-08-20-direct-publication-flow.md`
- Modify: `docs/telegram/content-backlog.md`
- Save verified screenshots under `docs/telegram/assets/2026-08-20-direct-publication-flow/`.

- [ ] **Step 1: Perform read-only preflight**

Verify no parallel rollout, zero active generation runs, database revision,
app/worker identities, available disk, current static release, and a fresh
rollback tuple. Preserve `kaigo.online` unchanged.

- [ ] **Step 2: Build one immutable release identity**

Build the app/migration image and frontend static archive from the same pushed
full SHA. Verify both identities before any switch. Do not deploy the frontend
alone.

- [ ] **Step 3: Recreate only required services in safe order**

Apply migrations only if head changed. Recreate app from the new image, verify
health/readiness and exact release identity, then atomically switch the static
release. Builder worker remains untouched unless its source/image changed.

- [ ] **Step 4: Verify the reported project safely**

Use the existing active Founder grant and verified project. Do not create a
second grant. Retry first publication through the real UI or authenticated
owner route and require HTTP 201, a persisted publication/release, exact source
origin, embed URL, and runtime URL.

- [ ] **Step 5: Verify in the built-in browser**

At 1920×1080 and 390×844 capture:

- direct Founder/paid access screen;
- progress or a deterministic presentation fixture of it;
- successful installation screen;
- external allowed-origin launcher closed/open/reopened.

Check visible copy, focus, link targets, stable geometry, one iframe, no
horizontal overflow, and no console errors.

- [ ] **Step 6: Update the editorial contour**

Record the root cause, joint release, exact verified behavior, tests,
production URLs, and screenshots without emails, secrets, private addresses,
cookies, or stable keys.

- [ ] **Step 7: Push and report**

Push the completed branch, report the full SHA and immutable release identity,
and give the user direct Studio, `/install`, and dedicated public canary URLs.
