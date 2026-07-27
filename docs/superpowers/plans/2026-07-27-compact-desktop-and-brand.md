# Kaigo Compact Desktop and Brand Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Kaigo landing fit common 125%-scaled desktop viewports, replace the ambiguous mark with a clear `K`, and add a branded favicon and descriptive browser title.

**Architecture:** Keep the current React structure and motion program. Add one isolated CSS breakpoint for 1281–1600 CSS pixels, keep the reusable `KaigoLogo` component as the single logo source, and use a separate minimal SVG favicon derived from the same geometry. Extend Playwright with a compact-desktop project so the layout contract is executable.

**Tech Stack:** React 19, TypeScript 6, CSS, Vite 8, Vitest, Playwright.

---

### Task 1: Add failing compact-desktop and brand contracts

**Files:**
- Modify: `frontend/playwright.config.ts`
- Modify: `frontend/e2e/landing.spec.ts`
- Modify: `frontend/src/landing/AccessibilityContracts.test.tsx`

- [ ] **Step 1: Write the failing tests**

Add a `compact-1536` Playwright project with viewport `1536×830` and a test tagged
`@compact` that asserts:

```ts
await expect(page).toHaveTitle('Kaigo — AI в вашем бизнесе за 10 минут');
await expect(page.locator('link[rel="icon"]')).toHaveAttribute('href', '/favicon.svg');
expect((await page.locator('.site-header').boundingBox())?.height).toBeLessThanOrEqual(96);
expect(parseFloat(await page.locator('.hero-copy h1').evaluate((node) => getComputedStyle(node).fontSize))).toBeLessThanOrEqual(50);
expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
await expect(page.getByTestId('hero-scene')).toHaveAttribute('data-motion-phase', 'complete', { timeout: 12_000 });
```

Extend the logo unit contract:

```ts
expect(logo.querySelector('[data-kaigo-mark="K"]')).toBeInTheDocument();
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
cd frontend
npm test -- AccessibilityContracts.test.tsx
npx playwright test e2e/landing.spec.ts --project=compact-1536 --grep @compact
```

Expected: the unit test fails because the current mark has no `K` contract; the
browser test fails because the current header is 112 px and the title/favicon are
missing.

- [ ] **Step 3: Commit the failing contracts**

```powershell
git add frontend/playwright.config.ts frontend/e2e/landing.spec.ts frontend/src/landing/AccessibilityContracts.test.tsx
git commit -m "test: define compact desktop and brand contracts"
```

### Task 2: Generate and implement the Kaigo K mark

**Files:**
- Create: `docs/evidence/2026-07-27-kaigo-k-mark/concept.png`
- Create: `frontend/public/favicon.svg`
- Modify: `frontend/src/shared/KaigoLogo.tsx`
- Modify: `frontend/index.html`

- [ ] **Step 1: Generate a vector-friendly mark direction**

Generate one square logo-brand concept: a distinctive geometric Latin `K`, warm
modern SaaS character, rounded precision, current sage-to-teal palette, no
wordmark, no mockup, no decorative background. Save it as the evidence asset and
use it as visual direction rather than shipping the raster in the UI.

- [ ] **Step 2: Implement one deterministic SVG geometry**

Replace the old `R`-like paths in `KaigoLogo.tsx` with a clear `K` made from a
vertical stem and two diagonal arms. Mark the SVG with:

```tsx
<svg className="kaigo-logo__mark" viewBox="0 0 42 42" data-kaigo-mark="K" aria-hidden="true">
```

Create `frontend/public/favicon.svg` with the same silhouette and enough spacing
to remain legible at 16×16.

- [ ] **Step 3: Update the document metadata**

Add to `frontend/index.html`:

```html
<link rel="icon" type="image/svg+xml" href="/favicon.svg" />
<title>Kaigo — AI в вашем бизнесе за 10 минут</title>
```

- [ ] **Step 4: Run the unit contract and verify GREEN**

Run:

```powershell
cd frontend
npm test -- AccessibilityContracts.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit the brand update**

```powershell
git add docs/evidence/2026-07-27-kaigo-k-mark/concept.png frontend/public/favicon.svg frontend/src/shared/KaigoLogo.tsx frontend/index.html frontend/src/landing/AccessibilityContracts.test.tsx
git commit -m "feat: add Kaigo K mark and favicon"
```

### Task 3: Implement the compact desktop CSS

**Files:**
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Add the isolated breakpoint**

Insert before the existing `max-width: 1280px` rules:

```css
@media (min-width: 1281px) and (max-width: 1600px) {
  .site-header { height: 94px; }
  .site-header__inner,
  .hero-section__inner { width: min(calc(100% - 88px), var(--container)); }
  .kaigo-logo__mark { width: 36px; height: 36px; }
  .kaigo-logo__wordmark { font-size: 30px; }
  .site-header__nav { gap: 36px; }
  .site-header__nav a { font-size: 16px; }
  .site-header__cta { min-height: 50px; padding-inline: 24px; font-size: 16px; }
  .hero-section { min-height: calc(100dvh - 94px); padding-block: 28px 40px; }
  .hero-copy h1 { font-size: clamp(46px, 3.15vw, 50px); }
  .hero-copy > p { font-size: 18px; }
  .hero-scene { transform: scale(0.86); transform-origin: center; }
}
```

Adjust only values proven necessary by the 1536×830 browser measurement. Do not
change animation timing or component markup.

- [ ] **Step 2: Run the compact browser contract and verify GREEN**

Run:

```powershell
cd frontend
npx playwright test e2e/landing.spec.ts --project=compact-1536 --grep @compact
```

Expected: PASS with no horizontal overflow and a completed visible hero cycle.

- [ ] **Step 3: Commit the responsive update**

```powershell
git add frontend/src/styles.css frontend/playwright.config.ts frontend/e2e/landing.spec.ts
git commit -m "fix: add compact desktop landing mode"
```

### Task 4: Verify, document, publish and deploy

**Files:**
- Create: `docs/evidence/2026-07-27-compact-desktop/README.md`
- Create: `docs/evidence/2026-07-27-compact-desktop/hero-1536x830.png`
- Create: `docs/evidence/2026-07-27-compact-desktop/hero-1920x1080.png`
- Modify: `docs/product-journal/2026-07.md`
- Modify: `docs/telegram/content-backlog.md`

- [ ] **Step 1: Run the complete verification suite**

```powershell
cd frontend
npm test
npm run lint
npm run build
npx playwright test
```

Expected: all commands exit with code 0.

- [ ] **Step 2: Verify visually in the in-app browser**

Check `1536×830`, `1920×1080`, `1366×768` and `390×844`. Confirm the complete
hero state, header proportions, K mark, favicon, title, no overlap and no
horizontal scroll. Save the two desktop evidence screenshots.

- [ ] **Step 3: Update verified project notes**

Record only confirmed results and link the evidence directory. Add a content
backlog item about supporting Windows 125% scaling without asking users to zoom
out.

- [ ] **Step 4: Commit and push**

```powershell
git add docs/evidence/2026-07-27-compact-desktop docs/product-journal/2026-07.md docs/telegram/content-backlog.md
git commit -m "docs: record compact desktop release"
git push origin codex/gemini-technical-foundation
```

- [ ] **Step 5: Deploy through the immutable release script**

On `/root/ai_project`, pull the branch, run
`bash scripts/deploy_marketing_site.sh "$(git rev-parse HEAD)"`, then verify the
public HTML, hashed assets, favicon, title, protected Studio route, API health and
the four target viewports on `https://kaigo.space/`.
