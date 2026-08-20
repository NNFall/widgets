# Simple Widget Publication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove manual domain entry from the customer publication flow, return copyable installation artifacts immediately, and publish a stable unauthenticated installation guide.

**Architecture:** Keep the existing exact-origin backend security contract. A first publish omits `allowed_domains`, so `PublicationService` derives the project source origin; an update sends the server-restored allowlist unchanged. Add a small public React route at `/install` with static, non-secret instructions and link to it from the successful publication handoff.

**Tech Stack:** React 19, TypeScript, Vitest/Testing Library, Playwright, aiohttp/SQLAlchemy publication service, CSS, nginx SPA fallback.

---

## File map

- Modify `frontend/src/studio/UpgradeGate.tsx`: remove editable domains, preserve the server allowlist on update, expose two copy actions and the guide link.
- Modify `frontend/src/studio/UpgradeGate.test.tsx`: publication payload and handoff regressions.
- Create `frontend/src/landing/WidgetInstallationPage.tsx`: public installation guide.
- Create `frontend/src/landing/WidgetInstallationPage.test.tsx`: route content and safe sample contract.
- Modify `frontend/src/App.tsx` and `frontend/src/App.test.tsx`: exact `/install` route.
- Modify `frontend/src/styles.css`: responsive guide and publication handoff layout.
- Modify `frontend/e2e/studio.spec.ts`: desktop/mobile customer flow without domain editing.
- Modify `frontend/e2e/presentation.spec.ts`: full-modal visual contract if affected by changed controls.
- Modify `docs/product-journal/2026-08.md`, `docs/telegram/release-packets/`, and `docs/telegram/content-backlog.md`: verified product record required by `AGENTS.md`.

### Task 1: Define the simplified publication contract

**Files:**
- Modify: `frontend/src/studio/UpgradeGate.test.tsx`
- Modify: `frontend/e2e/studio.spec.ts`

- [ ] **Step 1: Write failing component tests**

Add assertions that the active unpublished state has no control named
`На каких сайтах разрешить виджет`, publishes with this exact first payload,
and exposes the new handoff after success:

```ts
expect(screen.queryByLabelText('На каких сайтах разрешить виджет')).not.toBeInTheDocument();
expect(publishProject).toHaveBeenCalledWith(
  'project-1',
  {
    project_version_id: 'version-1',
    expected_active_release_id: null,
  },
  'csrf-token',
);
expect(screen.getByRole('button', { name: 'Скопировать код установки' })).toBeVisible();
expect(screen.getByRole('button', { name: 'Скопировать ссылку загрузчика' })).toBeVisible();
expect(screen.getByRole('link', { name: 'Открыть инструкцию по установке' }))
  .toHaveAttribute('href', '/install');
```

Add a restored-publication test proving an update sends the existing exact list:

```ts
expect(publishProject).toHaveBeenCalledWith(
  'project-1',
  expect.objectContaining({
    expected_active_release_id: 'release-1',
    allowed_domains: ['https://example.com'],
  }),
  'csrf-token',
);
```

Repeat the assertion with `allowed_domains: []`: explicit empty is a persisted
deny-all policy, while omission means reset to the project source origin.
Add a restoration-failure test proving the publish action remains disabled and
the UI asks the user to reload instead of offering a potentially destructive
legacy publish.

- [ ] **Step 2: Run the focused tests and record RED**

Run:

```powershell
cd frontend
npm.cmd test -- src/studio/UpgradeGate.test.tsx --run
```

Expected: failures because the textarea still exists, first publish sends its
value, and the link-copy/guide controls do not exist.

- [ ] **Step 3: Update the Playwright expectation before implementation**

Replace the domain-fill sequence with assertions that the field is absent,
the publish request omits domains on first publish, and the successful modal
contains both copy buttons and `/install` link. Keep both existing desktop and
mobile projects.

- [ ] **Step 4: Commit only the RED contract**

```powershell
git add frontend/src/studio/UpgradeGate.test.tsx frontend/e2e/studio.spec.ts
git commit -m "test: define simple widget publication"
```

### Task 2: Implement one-action publication and handoff

**Files:**
- Modify: `frontend/src/studio/UpgradeGate.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Remove editable-domain state and rendering**

Delete `allowedDomains`, `setAllowedDomains`, the label/textarea/help paragraph,
and all restoration writes into that state. Keep `publication.allowed_domains`
from the API response as the only source of restored domains.

- [ ] **Step 2: Build the publish payload with exact compatibility behavior**

Use the existing publication only for an update, including an explicit empty
list:

```ts
const domainPayload = publication
  ? { allowed_domains: publication.allowed_domains }
  : {};
```

Spread `domainPayload` into both versioned and legacy payloads. The first
publish therefore lets the backend derive `Project.source_url`; updates do not
silently replace an old operational allowlist.

- [ ] **Step 3: Replace domain-oriented copy and errors**

Use:

```ts
const description = publication
  ? 'Виджет опубликован. Скопируйте код установки или постоянную ссылку ниже.'
  : 'Ничего настраивать не нужно: сайт проекта будет разрешён автоматически. После публикации появится готовый код.';
```

Fallback failure text must be
`Не удалось опубликовать виджет. Попробуйте ещё раз.` and must not suggest that
the user check a field that no longer exists. Primary action is
`Опубликовать и получить код` before the first publish.

- [ ] **Step 4: Add independent copy state for code and loader URL**

Replace the single state with:

```ts
type CopyTarget = 'code' | 'link' | null;
const [copiedTarget, setCopiedTarget] = useState<CopyTarget>(null);
const [copyError, setCopyError] = useState(false);
```

Use one helper accepting a value and target. Render:

```tsx
<button type="button" onClick={() => void copyText(embedSnippet, 'code')}>
  Скопировать код установки
</button>
<button type="button" onClick={() => void copyText(publication.embed_url, 'link')}>
  Скопировать ссылку загрузчика
</button>
<a href="/install">Открыть инструкцию по установке</a>
```

Show the literal code in a selectable `<code>` block even when Clipboard API
is unavailable.

- [ ] **Step 5: Make the handoff responsive**

Add a two-column desktop action row that becomes one column below 720px. Keep
buttons at least 44px high, code horizontally scrollable, and the guide link
visibly secondary. Do not change the Founder offer layout in this task.

- [ ] **Step 6: Run focused tests and commit GREEN**

```powershell
cd frontend
npm.cmd test -- src/studio/UpgradeGate.test.tsx --run
cd ..
git add frontend/src/studio/UpgradeGate.tsx frontend/src/styles.css frontend/src/studio/UpgradeGate.test.tsx
git commit -m "feat: simplify widget publication handoff"
```

Expected: focused component file passes.

### Task 3: Add the public installation guide

**Files:**
- Create: `frontend/src/landing/WidgetInstallationPage.tsx`
- Create: `frontend/src/landing/WidgetInstallationPage.test.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Write failing page and routing tests**

Require exact `/install` routing, accessible headings, a safe sample, and no
real-looking stable key:

```ts
window.history.replaceState({}, '', '/install');
render(<App />);
expect(screen.getByRole('heading', { name: 'Как установить виджет Kaigo' })).toBeVisible();
expect(screen.getByRole('heading', { name: 'Tilda' })).toBeVisible();
expect(screen.getByRole('heading', { name: 'Обычный HTML-сайт' })).toBeVisible();
expect(screen.getByRole('heading', { name: 'WordPress' })).toBeVisible();
expect(screen.getByRole('heading', { name: 'Webflow' })).toBeVisible();
expect(screen.getByRole('heading', { name: 'Wix и другие конструкторы' })).toBeVisible();
expect(screen.getByText(/YOUR_WIDGET_KEY/)).toBeVisible();
```

- [ ] **Step 2: Run tests and record RED**

```powershell
cd frontend
npm.cmd test -- src/App.test.tsx src/landing/WidgetInstallationPage.test.tsx --run
```

Expected: missing module/page and `/install` currently renders the landing.

- [ ] **Step 3: Implement the exact public route**

In `App.tsx`, render `<WidgetInstallationPage />` only when
`pathname === '/install'`; `/installation-preview` must still render the landing.

- [ ] **Step 4: Implement concise official-path instructions**

The page must say:

- HTML: paste the Studio `<script ... async></script>` before `</body>` and deploy.
- Tilda: use block T123 for one page or the site/page custom HTML areas described
  in Tilda Help; republish the page/site.
- WordPress: use Custom HTML on a plugin-enabled/paid WordPress.com site, or a
  trusted site-wide code mechanism on self-hosted WordPress; restricted
  `script` tags may be removed on unsupported plans.
- Webflow: Page/Site settings → Custom code → Before `</body>`; save and publish.
- Wix: Dashboard → Settings → Custom Code → Add Custom Code → Body end → all
  pages; apply and publish.

Include links to the corresponding official support pages with
`target="_blank" rel="noopener noreferrer"`.

- [ ] **Step 5: Style and verify the page**

Use the existing landing palette, a readable max width, a sticky/simple header
link back to `/studio`, numbered cards, and code that wraps or scrolls without
horizontal page overflow at 390px.

- [ ] **Step 6: Run tests and commit**

```powershell
cd frontend
npm.cmd test -- src/App.test.tsx src/landing/WidgetInstallationPage.test.tsx --run
cd ..
git add frontend/src/App.tsx frontend/src/App.test.tsx frontend/src/landing/WidgetInstallationPage.tsx frontend/src/landing/WidgetInstallationPage.test.tsx frontend/src/styles.css
git commit -m "feat: add widget installation guide"
```

### Task 4: Verify compatibility and browser layout

**Files:**
- Modify: `frontend/e2e/studio.spec.ts`
- Modify: `frontend/e2e/presentation.spec.ts` to assert the changed handoff controls remain inside the full publication modal.
- Existing backend tests: `tests/saas_cases/test_publication.py`

- [ ] **Step 1: Run backend origin-security regressions**

```powershell
python -m pytest tests/saas_cases/test_publication.py -k "omitted_domains_uses_project_source_origin or public_chat_is_bound_to_active_release_and_approved_embed_origin" -q
```

Expected: both exact-origin contracts pass with no backend code change.

- [ ] **Step 2: Run focused Playwright at desktop and mobile**

```powershell
cd frontend
npx.cmd playwright test e2e/studio.spec.ts --project=desktop-1920 --project=mobile-390 --grep "publication"
```

Expected: no domain field, no horizontal overflow, publish-to-handoff works, and
the guide link resolves.

- [ ] **Step 3: Run complete frontend gates**

```powershell
cd frontend
npm.cmd test -- --run
npm.cmd run typecheck
npm.cmd run lint -- --no-cache
npm.cmd run build
```

Expected: all commands exit 0.

- [ ] **Step 4: Commit final test adjustments**

```powershell
git add frontend/e2e/studio.spec.ts frontend/e2e/presentation.spec.ts
git commit -m "test: verify simple publication at desktop and mobile"
```

### Task 5: Independent review, product record, and live acceptance

**Files:**
- Modify: `docs/product-journal/2026-08.md`
- Create/update: `docs/telegram/release-packets/2026-08-20-simple-widget-publication.md`
- Modify: `docs/telegram/content-backlog.md`

- [ ] **Step 1: Run Anti-Gravity review on the frozen diff**

Ask for a read-only review of publication payload compatibility, origin
security, Clipboard fallbacks, 390px layout, and instruction accuracy. Fix only
validated findings and rerun affected tests.

- [ ] **Step 2: Update the editorial contour with verified facts only**

Record implementation/test results separately from production deployment.
Never include emails, cookies, stable keys, private IP addresses, or secrets.

- [ ] **Step 3: Perform live read-only preflight before release**

Verify current immutable release, `ai_project` compose identity, DB revision,
app/worker readiness, available disk, rollback tuple, and absence of a parallel
rollout. Do not deploy from `/root/ai_project` dirty checkout.

- [ ] **Step 4: After a safe release, perform public acceptance**

Use the dedicated owner/canary workflow to publish on the exact allowed HTTPS
origin, then verify in the built-in browser at 1920×1080 and 390×844:

- Studio has no domain field and returns code/link;
- `/install` is public and readable;
- allowed canary loads one iframe, launcher open/close remains stable, and chat
  returns a non-empty answer;
- denied canary cannot render a usable launcher;
- no owner credentials appear on public canary requests.

- [ ] **Step 5: Final verification and status**

```powershell
git status --short --branch
git diff --check
```

Report local commits, production release ID if deployed, direct Studio/guide/
canary URLs, desktop/mobile evidence, exact test counts, and any remaining
release blocker separately.
