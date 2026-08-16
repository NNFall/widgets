# Stored Feedback Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Kaigo's provisional mailto contact flow with a real, server-backed feedback client that collects no contact details, preserves honest error states, and reduces the landing footer to a compact utility row.

**Architecture:** A focused shared API module owns the `GET /api/feedback/session` and `POST /api/feedback` contracts. The reusable React composer owns only form state and calls that module; landing and Studio provide a finite source plus an existing Studio CSRF token when available. The backend remains a separate technical-chat change: frontend tests mock the exact contract and never present success without a valid `stored` receipt.

**Tech Stack:** React 19, TypeScript 6, Vite 8, Vitest, Testing Library, Playwright, existing Manrope/Phosphor UI, PostgreSQL contract owned by the backend chat.

---

## File map

- `frontend/src/shared/contact.ts`: finite topics, consent path/version, operator status; no mailbox or mail composition.
- `frontend/src/shared/feedbackApi.ts`: request/response types, session bootstrap, idempotency keys, validation of the server receipt.
- `frontend/src/shared/FeedbackComposer.tsx`: reusable form state for landing and Studio.
- `frontend/src/landing/ContactSection.tsx`: landing copy around the shared composer.
- `frontend/src/landing/SiteFooter.tsx`: compact utility footer.
- `frontend/src/studio/StudioContactPanel.tsx`: Studio wrapper around the same composer.
- `frontend/src/studio/StudioPage.tsx` and `StudioProjectWorkbench.tsx`: pass the server CSRF token into the contact drawer.
- `frontend/src/auth/AuthGate.tsx`: safe link to the landing contact form when Studio cannot load.
- `frontend/src/legal/LegalPage.tsx` and `legalDocuments.tsx`: replace the nonexistent mailbox with the real contact-form route and accurate stored-feedback wording.
- `frontend/src/styles.css` and `frontend/src/mobile/foundations.css`: form states and compact responsive footer.
- Existing unit/E2E files: invert mailto assertions and cover the real network state machine.
- `docs/CONTACT_AND_LEGAL_HANDOFF.md`: final backend request and PostgreSQL/Telegram boundary.
- `docs/release-evidence/2026-08-17-product-ui-stored-feedback.md`: verified frontend evidence and explicit backend blocker.

### Task 1: Shared feedback contract and API client

**Files:**
- Create: `frontend/src/shared/feedbackApi.ts`
- Create: `frontend/src/shared/feedbackApi.test.ts`
- Modify: `frontend/src/shared/contact.ts`
- Modify: `frontend/src/shared/contact.test.ts`

- [ ] **Step 1: Replace the mail contract test with a failing stored-feedback contract**

Update `contact.test.ts` so it imports only `CONTACT_CONFIG`, `feedbackTopics`,
`isFeedbackReady`, and `FeedbackTopicId`. Assert that the config has no
`supportEmail` or `subjectPrefix` and that readiness depends only on message text:

```ts
expect('supportEmail' in CONTACT_CONFIG).toBe(false);
expect('subjectPrefix' in CONTACT_CONFIG).toBe(false);
expect(CONTACT_CONFIG.consentDocumentVersion).toBe('feedback-v2');
expect(feedbackTopics.map(({ id }) => id)).toEqual([
  'question', 'bug', 'improvement', 'cooperation',
]);
expect(isFeedbackReady('   ')).toBe(false);
expect(isFeedbackReady(' Идея ')).toBe(true);
```

- [ ] **Step 2: Add failing API tests**

Create `feedbackApi.test.ts` with fetch fixtures covering:

```ts
const session = await fetchFeedbackSession();
expect(fetchMock).toHaveBeenCalledWith('/api/feedback/session', {
  credentials: 'include',
  headers: { Accept: 'application/json' },
  signal: undefined,
});
expect(session).toEqual({
  csrfToken: 'csrf-feedback',
  consentVersion: 'feedback-v2',
  messageMaxLength: 4000,
});
```

For submission, assert exactly one POST with `credentials: 'include'`,
`Content-Type`, `Accept`, `X-CSRF-Token`, and `Idempotency-Key`. Parse the JSON
body and assert that `email`, `contact`, `name`, `project_id`, `run_id`, and
`user_id` are absent. Add cases for:

```ts
await expect(submitFeedback(input)).resolves.toEqual({
  receiptId: 'fb_receipt',
  status: 'stored',
  receivedAt: '2026-08-17T10:00:00Z',
});
await expect(submitFeedback(inputWith202)).rejects.toMatchObject({ status: 202 });
await expect(submitFeedback(invalidReceipt)).rejects.toThrow('invalid_feedback_receipt');
```

- [ ] **Step 3: Run RED tests**

Run:

```powershell
npx vitest run src/shared/contact.test.ts src/shared/feedbackApi.test.ts --reporter=verbose
```

Expected: FAIL because mail-only exports still exist and `feedbackApi.ts` does not.

- [ ] **Step 4: Implement the minimal shared contract**

Reduce `contact.ts` to:

```ts
export const CONTACT_CONFIG = {
  operatorStatus: 'самозанятый, плательщик НПД',
  consentDocumentPath: '/personal-data-consent/',
  consentDocumentVersion: 'feedback-v2',
} as const;

export const feedbackTopics = [
  { id: 'question', label: 'Вопрос' },
  { id: 'bug', label: 'Ошибка' },
  { id: 'improvement', label: 'Идея по улучшению' },
  { id: 'cooperation', label: 'Сотрудничество' },
] as const;

export type FeedbackTopicId = (typeof feedbackTopics)[number]['id'];
export function isFeedbackReady(message: string) {
  return message.trim().length > 0;
}
```

Implement `feedbackApi.ts` with the exact finite types:

```ts
import type { FeedbackTopicId } from './contact';

export type FeedbackSource =
  | 'landing_contact'
  | 'studio_account'
  | 'studio_auth_error';

export type FeedbackSubmission = {
  topic: FeedbackTopicId;
  message: string;
  source: FeedbackSource;
  consent: { version: string; accepted: true };
};

export class FeedbackApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly retryAfter: number | null = null,
  ) {
    super(message);
  }
}

export async function fetchFeedbackSession(signal?: AbortSignal) {
  const response = await fetch('/api/feedback/session', {
    credentials: 'include',
    headers: { Accept: 'application/json' },
    signal,
  });
  if (!response.ok) throw new FeedbackApiError('feedback_session_failed', response.status);
  const payload = await response.json() as Record<string, unknown>;
  if (
    typeof payload.csrf_token !== 'string'
    || typeof payload.consent_version !== 'string'
    || typeof payload.message_max_length !== 'number'
  ) {
    throw new FeedbackApiError('invalid_feedback_session', response.status);
  }
  return {
    csrfToken: payload.csrf_token,
    consentVersion: payload.consent_version,
    messageMaxLength: payload.message_max_length,
  };
}

export async function submitFeedback(input: {
  payload: FeedbackSubmission;
  csrfToken: string;
  idempotencyKey: string;
  signal?: AbortSignal;
}) {
  const response = await fetch('/api/feedback', {
    method: 'POST',
    credentials: 'include',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      'X-CSRF-Token': input.csrfToken,
      'Idempotency-Key': input.idempotencyKey,
    },
    body: JSON.stringify(input.payload),
    signal: input.signal,
  });
  if (response.status !== 200 && response.status !== 201) {
    const seconds = Number(response.headers.get('Retry-After'));
    throw new FeedbackApiError(
      'feedback_submit_failed',
      response.status,
      Number.isFinite(seconds) ? seconds : null,
    );
  }
  const payload = await response.json() as Record<string, unknown>;
  if (
    payload.status !== 'stored'
    || typeof payload.receipt_id !== 'string'
    || typeof payload.received_at !== 'string'
  ) {
    throw new FeedbackApiError('invalid_feedback_receipt', response.status);
  }
  return {
    receiptId: payload.receipt_id,
    status: 'stored' as const,
    receivedAt: payload.received_at,
  };
}

export function createFeedbackIdempotencyKey() {
  return `feedback-${crypto.randomUUID()}`;
}
```

Normalize response field names to `csrfToken`, `consentVersion`,
`messageMaxLength`, `receiptId`, and `receivedAt`. Throw a typed
`FeedbackApiError` with `status` and parsed `Retry-After` for every non-success
or malformed success response.

- [ ] **Step 5: Run GREEN tests and commit**

Run the Task 1 Vitest command again. Expected: all Task 1 tests PASS.

```powershell
git add frontend/src/shared/contact.ts frontend/src/shared/contact.test.ts frontend/src/shared/feedbackApi.ts frontend/src/shared/feedbackApi.test.ts
git commit -m "feat(frontend): add stored feedback API contract"
```

### Task 2: Reusable submit state machine

**Files:**
- Modify: `frontend/src/shared/FeedbackComposer.tsx`
- Modify: `frontend/src/shared/FeedbackComposer.test.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Write failing component tests**

Replace mail-link expectations with a button and mocked network calls:

```ts
render(<FeedbackComposer source="landing_contact" />);
const submit = screen.getByRole('button', { name: 'Отправить' });
expect(submit).toBeDisabled();
await user.type(screen.getByRole('textbox', { name: 'Сообщение' }), 'Новая идея');
expect(submit).toBeEnabled();
await user.click(submit);
expect(await screen.findByRole('status')).toHaveTextContent(
  'Спасибо. Сообщение сохранено и поможет улучшать Kaigo',
);
```

Assert GET-session then POST on landing, direct POST with the supplied Studio
CSRF token, loading copy `Отправляем…`, disabled controls while pending, and a
legal note linking to `/personal-data-consent/`. Add an error test:

```ts
expect(await screen.findByRole('alert')).toHaveTextContent(
  'Не удалось отправить. Ваш текст остался в форме',
);
expect(screen.getByRole('textbox', { name: 'Сообщение' })).toHaveValue('Новая идея');
await user.click(screen.getByRole('button', { name: 'Повторить' }));
expect(postHeaders.map((headers) => headers.get('Idempotency-Key')))
  .toEqual(['feedback-fixed', 'feedback-fixed']);
```

Verify that changing the message after an error creates a new idempotency key.

- [ ] **Step 2: Run RED component tests**

Run:

```powershell
npx vitest run src/shared/FeedbackComposer.test.tsx --reporter=verbose
```

Expected: FAIL because the component still renders a mailto anchor.

- [ ] **Step 3: Implement the state machine**

Change props to:

```ts
type FeedbackComposerProps = {
  source: FeedbackSource;
  csrfToken?: string | null;
  className?: string;
};
```

Track `idle | sending | success | error`, the current message/topic, the active
AbortController, the current normalized request fingerprint, and its idempotency
key. On submit:

1. trim message;
2. reuse the key only when the fingerprint is unchanged;
3. use the supplied Studio token or call `fetchFeedbackSession`;
4. submit the finite payload with the authoritative consent version;
5. clear text only after a valid stored receipt;
6. retain text and key on all failures.

Render a real `<button type="submit">`, `aria-busy` on the form while sending,
`role="status"` for success, and `role="alert"` for errors. Abort an in-flight
request on unmount.

- [ ] **Step 4: Add minimal visual states**

In `styles.css`, keep the established form geometry and add scoped rules for:

```css
.feedback-composer[aria-busy='true'] .feedback-composer__topics,
.feedback-composer[aria-busy='true'] .feedback-composer__message {
  opacity: .62;
}
.feedback-composer__result[role='status'] { color: oklch(.39 .08 155); }
.feedback-composer__result[role='alert'] { color: oklch(.42 .16 32); }
```

Do not animate layout. Preserve a 56 px primary button and 44 px legal link.

- [ ] **Step 5: Run GREEN tests and commit**

Run Task 2 tests plus Task 1 tests. Expected: PASS.

```powershell
git add frontend/src/shared/FeedbackComposer.tsx frontend/src/shared/FeedbackComposer.test.tsx frontend/src/styles.css
git commit -m "feat(frontend): submit feedback without contact details"
```

### Task 3: Landing contact, legal copy, auth fallback, and compact footer

**Files:**
- Modify: `frontend/src/landing/ContactSection.tsx`
- Modify: `frontend/src/landing/ContactSection.test.tsx`
- Modify: `frontend/src/landing/SiteFooter.tsx`
- Modify: `frontend/src/landing/SiteFooter.test.tsx`
- Modify: `frontend/src/auth/AuthGate.tsx`
- Modify: `frontend/src/auth/AuthGate.test.tsx`
- Modify: `frontend/src/legal/LegalPage.tsx`
- Modify: `frontend/src/legal/legalDocuments.tsx`
- Modify: `frontend/src/legal/LegalPage.test.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/mobile/foundations.css`

- [ ] **Step 1: Write RED surface contracts**

Assert across the listed tests that rendered output contains neither
`support@kaigo.space` nor `mailto:`. Landing copy must say:

```text
Напишите сообщение. Оно сохранится в Kaigo, контактные данные указывать не нужно.
```

Auth fallback must link to `/#contact` with accessible name `Оставить сообщение`.
Legal copy must route withdrawal/support requests to `/#contact` without
claiming that Kaigo will reply personally.

For the footer, require only these groups:

```tsx
<footer>
  <a aria-label="Kaigo — главная">…</a>
  <nav aria-label="Ссылки в подвале">…contact + four legal links…</nav>
  <p>© 2026 Kaigo · Самозанятый, плательщик НПД · реквизиты уточняются</p>
</footer>
```

Assert that the removed product navigation and operator mail link are absent.

- [ ] **Step 2: Run RED tests**

Run:

```powershell
npx vitest run src/landing/ContactSection.test.tsx src/landing/SiteFooter.test.tsx src/auth/AuthGate.test.tsx src/legal/LegalPage.test.tsx --reporter=verbose
```

Expected: FAIL on the current provisional mail and four-column footer.

- [ ] **Step 3: Implement the truthful surfaces**

- Render `<FeedbackComposer source="landing_contact" />` in `ContactSection`.
- Delete channel rows and all imports of mail helpers.
- Replace `SupportContact` with a `marketingHref('/#contact')` link.
- Point legal contact/withdrawal actions at the same contact section.
- Remove all sentences describing a mail application, provisional mailbox, or
  promised response.
- Keep operator identifiers explicitly unconfirmed.

- [ ] **Step 4: Distill the footer CSS**

Use one wrapping row on desktop:

```css
.landing-page .site-footer__inner,
.legal-page .site-footer__inner {
  min-height: 0;
  padding-block: 18px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px 24px;
}
.site-footer__links { display: flex; flex-wrap: wrap; gap: 0 18px; }
.site-footer__links a { min-height: 44px; font-size: 12px; }
.site-footer__meta { margin: 0; font-size: 11px; }
```

At mobile widths use two link columns while retaining 44 px hit areas. Remove
the superseded four-column, tagline, operator-card, and mail-link rules rather
than stacking override layers.

- [ ] **Step 5: Run GREEN tests and commit**

Run the Task 3 test command and `npm run typecheck`. Expected: PASS.

```powershell
git add frontend/src/landing frontend/src/auth/AuthGate.tsx frontend/src/auth/AuthGate.test.tsx frontend/src/legal frontend/src/styles.css frontend/src/mobile/foundations.css
git commit -m "fix(frontend): remove provisional support and compact footer"
```

### Task 4: Studio integration

**Files:**
- Modify: `frontend/src/studio/StudioContactPanel.tsx`
- Modify: `frontend/src/studio/StudioContactPanel.test.tsx`
- Modify: `frontend/src/studio/StudioPage.tsx`
- Modify: `frontend/src/studio/StudioProjectWorkbench.tsx`
- Modify: `frontend/src/studio/SaasStudioFlow.test.tsx`
- Modify: `frontend/src/studio/StudioAccessibilityContracts.test.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Write RED Studio contracts**

Render the panel with `csrfToken="csrf-studio"` and assert:

```ts
expect(screen.queryByText('support@kaigo.space')).not.toBeInTheDocument();
expect(screen.queryByText('Telegram')).not.toBeInTheDocument();
expect(screen.getByRole('button', { name: 'Отправить' })).toBeVisible();
```

In `SaasStudioFlow.test.tsx`, open contact from both Studio home and account,
submit a message, and assert one POST with source `studio_account`, the supplied
CSRF token, and no client-supplied project/run IDs.

- [ ] **Step 2: Run RED Studio tests**

Run:

```powershell
npx vitest run src/studio/StudioContactPanel.test.tsx src/studio/SaasStudioFlow.test.tsx src/studio/StudioAccessibilityContracts.test.tsx --reporter=verbose
```

Expected: FAIL because the panel still displays mail and Telegram rows and does
not receive CSRF.

- [ ] **Step 3: Implement Studio wiring**

Change panel props to `{ csrfToken?: string | null }`, remove domain/project
serialization and channel icons, and render:

```tsx
<FeedbackComposer
  source="studio_account"
  csrfToken={csrfToken}
  className="studio-contact-panel__composer"
/>
```

Pass `controller.csrfToken` from `StudioPage` and
`StudioProjectWorkbench`. Keep the existing drawer focus trap, Escape handling,
body lock, and focus restoration unchanged.

- [ ] **Step 4: Remove obsolete Studio channel CSS**

Delete `.studio-contact-panel__channels`, `__channel`, and Telegram-specific
rules. Keep the intro and composer rules, with a readable 14 px explanatory
line and existing mobile one-column topic grid.

- [ ] **Step 5: Run GREEN Studio tests and commit**

Run the Task 4 tests and `npm run typecheck`. Expected: PASS.

```powershell
git add frontend/src/studio frontend/src/styles.css
git commit -m "feat(frontend): connect Studio feedback form contract"
```

### Task 5: Browser contracts and backend handoff

**Files:**
- Modify: `frontend/e2e/landing.spec.ts`
- Modify: `frontend/e2e/studio.spec.ts`
- Modify: `docs/CONTACT_AND_LEGAL_HANDOFF.md`
- Create: `docs/release-evidence/2026-08-17-product-ui-stored-feedback.md`
- Create: `docs/release-evidence/assets/2026-08-17-product-ui-stored-feedback/README.md`

- [ ] **Step 1: Replace E2E mail assertions with real request assertions**

Route both endpoints in Playwright:

```ts
await page.route('**/api/feedback/session', route => route.fulfill({
  status: 200,
  contentType: 'application/json',
  body: JSON.stringify({
    csrf_token: 'csrf-feedback',
    consent_version: 'feedback-v2',
    message_max_length: 4000,
  }),
}));
await page.route('**/api/feedback', async route => {
  captured.push({ headers: route.request().headers(), body: route.request().postDataJSON() });
  await route.fulfill({
    status: 201,
    contentType: 'application/json',
    body: JSON.stringify({
      receipt_id: 'fb_e2e', status: 'stored', received_at: '2026-08-17T10:00:00Z',
    }),
  });
});
```

Assert success, one POST, finite source, consent version, no contact fields, no
horizontal overflow, and no `mailto:` in landing/footer/Studio. Add a 500 case
that retains text and retries with the same idempotency key.

- [ ] **Step 2: Run browser tests**

Run the focused desktop and mobile tests at 390×844 plus the existing 320 px
mobile project. Expected: PASS with mocked endpoint; no claim of production
storage.

- [ ] **Step 3: Update the backend handoff**

Replace the old future `202 queued` contract with `GET /api/feedback/session`
and `201 stored`, remove `name` and `contact`, document the PostgreSQL table,
server-derived Studio context, CSRF, idempotency, rate limits, retention,
operator history, and future transactional Telegram outbox. Add an explicit
`BACKEND REQUEST` block containing problem, current frontend, endpoints,
contract, user benefit, temporary behavior, and acceptance criteria.

- [ ] **Step 4: Record evidence without Telegram-editor files**

The evidence document must state:

- frontend form and compact footer are implemented;
- E2E uses a contract mock;
- production endpoint/table are not implemented by this branch;
- no live success is claimed;
- backend chat must integrate the named commit before any production deploy.

Save real local browser captures into the evidence asset folder and link them
from its README. Do not edit `docs/telegram/**`.

- [ ] **Step 5: Commit**

```powershell
git add frontend/e2e docs/CONTACT_AND_LEGAL_HANDOFF.md docs/release-evidence
git commit -m "test(frontend): verify stored feedback journey"
```

### Task 6: Full verification, review, and handoff

**Files:**
- Modify only if a failing regression requires a scoped fix.

- [ ] **Step 1: Run the full frontend gate**

```powershell
npm run typecheck
npm run lint
npm run test -- --run
npm run build
git diff --check
```

Expected: zero TypeScript/lint/build errors and all frontend Vitest tests PASS.

- [ ] **Step 2: Run the responsive visual matrix**

Use the in-app browser at 1920×1080, 1536×960, 390×844, and 320 px. Check:

- compact footer height and wrapping;
- no horizontal overflow;
- form input, loading, success, error, and retry;
- Studio drawer focus/Escape/restore behavior;
- 44 px targets and readable contrast;
- reduced motion.

- [ ] **Step 3: Request independent review**

Ask one reviewer for spec compliance and another for Critical/Important code,
security, responsive, and accessibility issues. Fix only confirmed findings,
starting every behavioral fix with a failing regression test.

- [ ] **Step 4: Push and prepare technical handoff**

Push only `codex/product-ui`. Report the final frontend commit, touched files,
test evidence, screenshots, and the exact `BACKEND REQUEST`. Do not deploy
production, run migrations, restart services, edit nginx, or modify
`/root/ai_project`.

- [ ] **Step 5: Release boundary**

Do not publish v12 as a working public submission flow until the backend chat
has implemented and deployed the endpoint. If a visual-only preview is needed,
label its send action unavailable rather than returning fabricated success.
