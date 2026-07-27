# Kaigo Marketing Site and Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Собрать адаптивный маркетинговый сайт Kaigo по восьми утверждённым референсам, воспроизвести hero-сцену с управляемой анимацией и создать визуально согласованную Studio поверх существующего Builder API.

**Architecture:** Новый `frontend/` — самостоятельное React/Vite SPA с маршрутами `/` и `/studio`. Лендинг, motion-примитивы и студия используют общие design tokens; существующие Python endpoints, SSE, sandboxed preview и `postMessage`-протокол не переписываются. Production build является статическим артефактом, а Studio обращается к builder-lab через настраиваемый префикс `/builder/`.

**Tech Stack:** React, TypeScript, Vite, Motion for React, Phosphor Icons, Vitest, React Testing Library, Playwright, axe-core, CSS custom properties.

---

## Файловая карта

```text
frontend/
  index.html                         HTML entrypoint и метаданные
  package.json                       команды dev/test/build/e2e
  package-lock.json                  зафиксированные версии npm
  tsconfig.json                      строгий TypeScript
  vite.config.ts                     SPA build и локальный proxy /builder
  eslint.config.js                   TypeScript/React lint rules
  vitest.setup.ts                    DOM matchers и cleanup
  playwright.config.ts               1920×1080, 390×844, reduced motion
  public/assets/house-cutout.png      generated + background-removed house
  src/main.tsx                       React mount
  src/App.tsx                        выбор LandingPage или StudioPage
  src/styles.css                     tokens, layout, responsive, motion
  src/shared/KaigoLogo.tsx           общий логотип
  src/shared/UrlComposer.tsx         URL form, validation, переход в Studio
  src/shared/BrowserMockup.tsx       переиспользуемый макет сайта
  src/shared/Reveal.tsx              scroll reveal с reduced-motion
  src/landing/LandingPage.tsx        структура экранов 01–08
  src/landing/HeroSection.tsx        copy + composer + hero scene
  src/landing/HeroOrbitScene.tsx     state machine и Perspective Orbit
  src/landing/HowItWorksSection.tsx  экран 02
  src/landing/AnalysisSection.tsx    экран 03
  src/landing/CaseStudySection.tsx   экран 04
  src/landing/CapabilitiesSection.tsx экран 05
  src/landing/StudioSection.tsx      экран 06 и ссылка в рабочую Studio
  src/landing/FaqSection.tsx         экран 07
  src/landing/FinalCtaSection.tsx    экран 08 + footer
  src/studio/api.ts                  типизированный Builder API client
  src/studio/useBuilderRun.ts        create/SSE/resume/cancel/retry/refine
  src/studio/StudioPage.tsx          светлая оболочка Studio
  src/studio/StudioTimeline.tsx      понятные статусы запуска
  src/studio/StudioPreview.tsx       sandbox iframe + desktop/mobile
  src/**/*.test.tsx                  component/unit regression
  e2e/landing.spec.ts                viewport/motion/overflow/screenshots
  e2e/studio.spec.ts                 Studio fake-API flow
deploy/nginx/kaigo-marketing-site.conf пример статического root + /builder proxy
scripts/deploy_marketing_site.sh      reproducible build/copy/reload script
```

## Неизменяемые контуры

- Не изменять семантику `POST /api/runs`, snapshot, SSE, cancel, retry, refine и preview/chat endpoints.
- Не добавлять `allow-same-origin` в sandbox iframe.
- Сохранить проверки `channel_id`, `revision`, `request_id` и версию `postMessage`-протокола.
- Сохранить localStorage key `kaigo.builder.activeRun.v1`.
- Не использовать PNG-референсы как production background; они служат только визуальной целью.
- Не запускать реальные Gemini-прогоны ради CSS-проверок.

### Task 1: Frontend scaffold и общий контракт страницы

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/eslint.config.js`
- Create: `frontend/vitest.setup.ts`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/App.test.tsx`
- Create: `frontend/src/styles.css`

- [ ] **Step 1: Создать failing contract test**

```tsx
render(<App />);
expect(screen.getByRole('heading', {
  name: 'Через 10 минут вы сможете сказать: наш бизнес использует AI',
})).toBeInTheDocument();
expect(screen.getByRole('link', { name: 'Перейти в студию' })).toHaveAttribute('href', '/studio');
expect(document.querySelectorAll('[data-landing-section]')).toHaveLength(8);
```

- [ ] **Step 2: Запустить RED**

Run: `cd frontend && npm test -- --run src/App.test.tsx`

Expected: FAIL, потому что `App`, маршруты и секции ещё не существуют.

- [ ] **Step 3: Установить и зафиксировать зависимости**

Run:

```powershell
cd frontend
npm install react react-dom motion @phosphor-icons/react @fontsource/manrope
npm install -D typescript vite @vitejs/plugin-react vitest jsdom @testing-library/react @testing-library/jest-dom @testing-library/user-event @types/react @types/react-dom @playwright/test @axe-core/playwright eslint typescript-eslint
```

- [ ] **Step 4: Реализовать минимальный SPA contract**

`App` выбирает страницу без стороннего router:

```tsx
export function App() {
  return window.location.pathname.startsWith('/studio')
    ? <StudioPage />
    : <LandingPage />;
}
```

До реализации секций `LandingPage` выводит восемь семантических placeholder-section с окончательными `id`; placeholders удаляются в Tasks 3–4.

- [ ] **Step 5: Запустить GREEN и typecheck**

Run:

```powershell
npm test -- --run src/App.test.tsx
npm run typecheck
```

Expected: PASS, 0 TypeScript errors.

- [ ] **Step 6: Commit**

```powershell
git add frontend
git commit -m "feat: scaffold Kaigo marketing frontend"
```

### Task 2: Generated house asset через imagegen и local background removal

**Files:**
- Create: `frontend/public/assets/house-source.png`
- Create: `frontend/public/assets/house-cutout.png`
- Create: `docs/evidence/marketing-site/house-cutout-preview.png`

- [ ] **Step 1: Сгенерировать source asset**

Use built-in image generation with the prompt:

```text
Use case: stylized-concept
Asset type: isolated illustration inside a landing-page browser mockup
Primary request: premium contemporary two-storey white architectural house with restrained glazing, one pale sage tree and minimal landscaping
Style/medium: soft editorial architectural visualization, clean and realistic, matching a warm cream B2B landing page
Composition/framing: centered three-quarter view, whole house visible, generous padding
Scene/backdrop: perfectly flat solid #ff00ff chroma-key background
Constraints: no text, no logo, no browser chrome, no people, no cast shadow, do not use #ff00ff in the subject
```

- [ ] **Step 2: Удалить фон установленным skill wrapper**

Run:

```powershell
& "C:\Users\User\.codex\skills\remove-background-local\scripts\remove-background.ps1" `
  -Source "frontend\public\assets\house-source.png" `
  -Output "frontend\public\assets\house-cutout.png" `
  -Aggressiveness "0.30"
```

- [ ] **Step 3: Проверить alpha и preview**

Открыть `PreviewPath` через `view_image`. Углы должны быть прозрачными, дом и дерево — без дыр, magenta fringe отсутствует. Если детали пропали — повторить с `soft`; если остался фон — с `0.50`.

- [ ] **Step 4: Commit**

```powershell
git add frontend/public/assets docs/evidence/marketing-site
git commit -m "assets: add Kaigo architectural hero cutout"
```

### Task 3: Hero contract и управляемая motion state machine

**Files:**
- Create: `frontend/src/shared/KaigoLogo.tsx`
- Create: `frontend/src/shared/UrlComposer.tsx`
- Create: `frontend/src/shared/BrowserMockup.tsx`
- Create: `frontend/src/landing/HeroSection.tsx`
- Create: `frontend/src/landing/HeroOrbitScene.tsx`
- Create: `frontend/src/landing/HeroOrbitScene.test.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Написать failing phase tests**

```tsx
vi.useFakeTimers();
render(<HeroOrbitScene />);
expect(screen.getByTestId('hero-scene')).toHaveAttribute('data-motion-phase', 'source');
await vi.advanceTimersByTimeAsync(1900);
expect(screen.getByTestId('hero-scene')).toHaveAttribute('data-motion-phase', 'scanning');
await vi.advanceTimersByTimeAsync(5600);
expect(screen.getByTestId('hero-scene')).toHaveAttribute('data-motion-phase', 'complete');
expect(screen.getAllByTestId('process-card')).toHaveLength(3);
expect(screen.getByTestId('widget-preview')).toBeVisible();
```

Отдельный тест mock-ит reduced-motion и ожидает `complete` сразу после mount.

- [ ] **Step 2: Запустить RED**

Run: `npm test -- --run src/landing/HeroOrbitScene.test.tsx`

Expected: FAIL, component missing.

- [ ] **Step 3: Реализовать state machine**

Фазы и пороги:

```ts
export type HeroPhase = 'source' | 'scanning' | 'transforming' | 'cards' | 'widget' | 'complete';
export const HERO_TIMELINE = [
  [1800, 'scanning'],
  [4400, 'transforming'],
  [4900, 'cards'],
  [6000, 'widget'],
  [7400, 'complete'],
] as const;
```

Каждый timeout очищается в `useEffect` cleanup. Классы фазы изменяют только `transform`, `opacity` и `stroke-dashoffset`.

- [ ] **Step 4: Воспроизвести геометрию 1920×1080**

- container `max-width: 1800px`, desktop split примерно `5/12 + 7/12`;
- H1 ограничен `10.8ch`, переносы зафиксированы через четыре `<span>`;
- browser source масштабируется `1.12 → 0.88` и уходит вправо `110px`;
- scan проходит сверху вниз за `2.6s`;
- три карточки появляются с задержкой `300ms`;
- SVG path рисуется `stroke-dashoffset`;
- widget появляется за `0.85s` и делает один coral pulse;
- animation не блокирует input и CTA.

- [ ] **Step 5: Реализовать mobile и reduced-motion**

На `<768px`: copy → composer → canvas, H1 `38–44px`, composer вертикальный, cards компактные, нет horizontal overflow. При `prefers-reduced-motion` финальное состояние появляется без таймеров и recurring motion.

- [ ] **Step 6: Запустить GREEN**

Run:

```powershell
npm test -- --run src/landing/HeroOrbitScene.test.tsx src/App.test.tsx
npm run typecheck
npm run build
```

Expected: PASS and successful Vite build.

- [ ] **Step 7: Commit**

```powershell
git add frontend/src frontend/public/assets
git commit -m "feat: build animated Kaigo hero"
```

### Task 4: Экраны 02–08 и scroll motion

**Files:**
- Create: `frontend/src/shared/Reveal.tsx`
- Create: `frontend/src/landing/HowItWorksSection.tsx`
- Create: `frontend/src/landing/AnalysisSection.tsx`
- Create: `frontend/src/landing/CaseStudySection.tsx`
- Create: `frontend/src/landing/CapabilitiesSection.tsx`
- Create: `frontend/src/landing/StudioSection.tsx`
- Create: `frontend/src/landing/FaqSection.tsx`
- Create: `frontend/src/landing/FinalCtaSection.tsx`
- Create: `frontend/src/landing/LandingPage.test.tsx`
- Modify: `frontend/src/landing/LandingPage.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Написать failing semantic/interaction tests**

```tsx
render(<LandingPage />);
expect(screen.getByRole('heading', { name: 'Как это работает' })).toBeVisible();
expect(screen.getByRole('heading', { name: 'Что видит Kaigo' })).toBeVisible();
expect(screen.getByRole('button', { name: 'После' })).toHaveAttribute('aria-pressed', 'true');
await user.click(screen.getByRole('button', { name: /Сколько времени/ }));
expect(screen.getByText(/первую версию/i)).toBeVisible();
expect(screen.getAllByRole('textbox', { name: /ссылка на сайт/i })).toHaveLength(2);
```

- [ ] **Step 2: Запустить RED**

Run: `npm test -- --run src/landing/LandingPage.test.tsx`

- [ ] **Step 3: Реализовать экраны по референсам**

- `HowItWorks`: 3 narrative panels с SVG route и живыми артефактами UI;
- `Analysis`: browser mockup + 4 callouts и magnifier reveal;
- `CaseStudy`: desktop before/after, mobile accessible toggle;
- `Capabilities`: центральный chat + 6 чередующихся features;
- `StudioSection`: interactive typed refinement + revision crossfade;
- `Faq`: native buttons с `aria-expanded`, spring height и клавиатурой;
- `FinalCta`: повторный composer, guarantees и footer.

- [ ] **Step 4: Добавить scroll reveals**

`Reveal` использует Motion `whileInView` с `viewport={{ once: true, amount: 0.22 }}`. Контент остаётся доступным без JavaScript и при reduced-motion.

- [ ] **Step 5: Запустить GREEN**

Run:

```powershell
npm test -- --run src/landing/LandingPage.test.tsx
npm run typecheck
npm run build
```

- [ ] **Step 6: Commit**

```powershell
git add frontend/src
git commit -m "feat: add animated Kaigo landing sections"
```

### Task 5: Рабочая Studio поверх Builder API

**Files:**
- Create: `frontend/src/studio/types.ts`
- Create: `frontend/src/studio/api.ts`
- Create: `frontend/src/studio/useBuilderRun.ts`
- Create: `frontend/src/studio/StudioTimeline.tsx`
- Create: `frontend/src/studio/StudioPreview.tsx`
- Create: `frontend/src/studio/StudioPage.tsx`
- Create: `frontend/src/studio/StudioPage.test.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Написать failing fake-API flow test**

```tsx
vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
  JSON.stringify({ run_id: 'run-123' }),
  { status: 202, headers: { 'Content-Type': 'application/json' } },
)));
render(<StudioPage />);
await user.type(screen.getByLabelText('Ссылка на сайт'), 'https://example.com');
await user.click(screen.getByRole('button', { name: 'Создать AI-виджет' }));
expect(await screen.findByText('Запуск создан')).toBeVisible();
expect(localStorage.getItem('kaigo.builder.activeRun.v1')).toBe('run-123');
```

Дополнительные tests: resume localStorage run, cancel/retry/refine, desktop/mobile preview, terminal error state.

- [ ] **Step 2: Запустить RED**

Run: `npm test -- --run src/studio/StudioPage.test.tsx`

- [ ] **Step 3: Реализовать API client и state hook**

```ts
const BUILDER_BASE = (import.meta.env.VITE_BUILDER_BASE_URL ?? '/builder/').replace(/\/?$/, '/');
export const builderUrl = (path: string) => new URL(path, new URL(BUILDER_BASE, location.origin)).toString();
```

`useBuilderRun` управляет `EventSource`, snapshot polling fallback, cleanup, resume и последней принятой ревизией. Ошибки показываются русским понятным текстом; raw provider message доступен под `Детали`.

- [ ] **Step 4: Реализовать Studio shell**

- кремовая оболочка и общий header;
- слева URL, brief, progress, timeline, refinement, retry/cancel;
- справа preview, desktop/mobile, status, token/time metrics;
- iframe остаётся `sandbox="allow-scripts"`;
- Preview/Publish визуально разделены, но Publish помечен `Скоро` и не выдаётся за работающий endpoint;
- active run восстанавливается после reload.

- [ ] **Step 5: Запустить GREEN**

Run:

```powershell
npm test -- --run src/studio/StudioPage.test.tsx
npm run typecheck
npm run build
```

- [ ] **Step 6: Commit**

```powershell
git add frontend/src/studio frontend/src/App.tsx frontend/src/styles.css
git commit -m "feat: add Kaigo studio frontend"
```

### Task 6: Playwright visual, responsive и accessibility gate

**Files:**
- Create: `frontend/playwright.config.ts`
- Create: `frontend/e2e/landing.spec.ts`
- Create: `frontend/e2e/studio.spec.ts`
- Create: `frontend/e2e/fixtures/builder.ts`
- Create: `frontend/e2e/landing.spec.ts-snapshots/*`

- [ ] **Step 1: Написать failing structural E2E**

Проверить на `1920×1080`:

```ts
await expect(page.getByRole('heading', { name: EXACT_HERO })).toBeVisible();
expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
await expect(page.locator('[data-motion-phase="complete"]')).toBeVisible({ timeout: 9000 });
await expect(page.locator('[data-testid="process-card"]')).toHaveCount(3);
```

На `390×844`: menu, composer, order copy-before-scene, no overflow, buttons minimum `44×44`.

- [ ] **Step 2: Запустить RED**

Run: `npx playwright test e2e/landing.spec.ts --project=desktop-1920`

- [ ] **Step 3: Довести hero визуально во встроенном браузере**

Сделать снимки:

- `hero-source-1920.png`;
- `hero-scan-1920.png`;
- `hero-complete-1920.png`;
- `landing-full-1920.png`;
- `landing-full-390.png`;
- `studio-desktop.png`;
- `studio-mobile.png`.

Сопоставить hero с `docs/research/assets/2026-07-27-kaigo-landing-full-sequence/01-hero-final.png`: поля, H1 line breaks, URL composer, масштаб mockup, позиции cards и widget.

- [ ] **Step 4: Accessibility и reduced-motion**

Run:

```powershell
npx playwright test --project=reduced-motion
npx playwright test --project=accessibility
```

Expected: no serious/critical axe findings; reduced-motion сразу показывает final hero; keyboard доступен для menu, forms, before/after и FAQ.

- [ ] **Step 5: Полный frontend gate**

Run:

```powershell
npm run lint
npm run typecheck
npm test -- --run
npm run build
npx playwright test
```

- [ ] **Step 6: Commit**

```powershell
git add frontend/e2e frontend/playwright.config.ts frontend/src docs/evidence/marketing-site
git commit -m "test: verify Kaigo landing and studio visuals"
```

### Task 7: Production packaging, deployment и редакторский контур

**Files:**
- Create: `deploy/nginx/kaigo-marketing-site.conf`
- Create: `scripts/deploy_marketing_site.sh`
- Create: `tests/deployment_cases/test_marketing_site_package.py`
- Modify: `docker-compose.yml` only if a dedicated static service is selected after live nginx audit
- Modify: `docs/KAIGO_SPACE_OPERATIONS.md`
- Modify: `docs/product-journal/2026-07.md`
- Create: `docs/telegram/release-packets/2026-07-27-kaigo-marketing-site.md`

- [ ] **Step 1: Написать packaging test**

Test должен проверить, что build содержит `index.html`, hashed JS/CSS/assets и что Nginx-пример проксирует `/builder/` без ослабления iframe/CSP.

- [ ] **Step 2: Запустить RED и затем GREEN**

Run:

```powershell
npm run build
python -m pytest -q tests/deployment_cases/test_marketing_site_package.py
```

- [ ] **Step 3: Проверить backend baseline**

Run:

```powershell
python -m pytest -q
python -m compileall -q app builder_lab scripts tests
python -m ruff check app builder_lab scripts tests
docker compose --profile builder-lab config --quiet
git diff --check
```

- [ ] **Step 4: Live nginx audit и deploy**

На сервере сначала read-only проверить `/etc/nginx/sites-available/kaigo.space`, текущий static root и `/builder/`. Затем загрузить versioned build, выполнить `nginx -t`, атомарно переключить symlink/static root, reload и проверить `/`, `/studio`, `/builder/api/...` и старые `/w/...` маршруты.

- [ ] **Step 5: Visual production smoke во встроенном браузере**

Проверить реальный домен на `1920×1080` и mobile, hero complete, FAQ, Studio fake/real shell, сохранность старых widget routes и отсутствие console/network errors.

- [ ] **Step 6: Обновить журнал и release packet**

Записать подтверждённые изменения, пути к evidence и понятную тему будущего Telegram-поста. Не публиковать Telegram-сообщение без отдельного разрешения.

- [ ] **Step 7: Final review, commit и push**

После spec compliance и code quality review:

```powershell
git add frontend deploy/nginx/kaigo-marketing-site.conf scripts/deploy_marketing_site.sh tests/deployment_cases/test_marketing_site_package.py docs/KAIGO_SPACE_OPERATIONS.md docs/product-journal/2026-07.md docs/telegram/release-packets/2026-07-27-kaigo-marketing-site.md docs/evidence/marketing-site
git commit -m "feat: launch Kaigo marketing site and studio"
git push origin codex/gemini-technical-foundation
```

Проверить, что remote SHA содержит финальный commit, а посторонние изменения не попали в историю.

## Финальные критерии

- Hero на `1920×1080` визуально совпадает с утверждённым `Perspective Orbit` и проходит всю 7,4-секундную историю.
- На mobile нет горизонтального скролла; сюжет анимации сохраняется.
- Экраны 02–08 являются настоящими DOM/UI секциями с осмысленными scroll/micro-анимациями.
- `/studio` создаёт и восстанавливает Builder run через существующий API; iframe sandbox и bridge не ослаблены.
- Reduced-motion, keyboard navigation и serious/critical axe gate проходят.
- Unit, build, Playwright, Python regression и production smoke имеют свежие доказательства.
- В GitHub и на сервере находится одна и та же проверенная версия.
