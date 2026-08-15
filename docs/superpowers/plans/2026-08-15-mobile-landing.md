# Kaigo Mobile Landing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перестроить лендинг Kaigo для телефонов 320–430 px и landscape так, чтобы весь смысловой контент был читаемым, ключевые визуалы не обрезались, формы и навигация работали с touch и клавиатуры, а desktop-композиция сохранилась.

**Architecture:** Существующий `styles.css` остаётся desktop-источником. Новый слой `src/mobile/index.css`, импортируемый после него, разделяется на `foundations.css`, `tour.css` и `sections.css`; каждый файл содержит только mobile/phone-landscape правила и не меняет desktop. Поведенческие изменения ограничены закрытием меню по Escape, фокусом ошибки URL и отключением autoplay Product Tour на телефоне.

**Tech Stack:** React 19, TypeScript 6, CSS, Motion, Vitest, Testing Library, Playwright, axe-core.

---

### Task 1: Актуализировать baseline и зафиксировать мобильные контракты в RED

**Files:**
- Modify: `frontend/src/landing/LandingPage.test.tsx`
- Modify: `frontend/src/landing/LandingVisualContracts.test.tsx`
- Modify: `frontend/src/landing/ProductTour.test.tsx`
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/e2e/landing.spec.ts`

- [ ] **Step 1: Убрать только устаревшие ожидания v8**

  Заменить количество секций `10` на `9`, старый hero на точный текущий `Через 10 минут вы сможете сказать: наш бизнес использует AI`, старый заголовок кейса на `Что меняется для посетителя сайта`, NovaFlow-ассеты на три FORMA-ассета и проверки удалённой кнопки autoplay на проверку отсутствия `.product-tour__autoplay`.

- [ ] **Step 2: Запустить только исправленные unit-файлы**

  Run:

  ```powershell
  npx vitest run src/App.test.tsx src/landing/LandingPage.test.tsx src/landing/LandingVisualContracts.test.tsx src/landing/ProductTour.test.tsx --reporter=verbose --maxWorkers=1 --pool=forks
  ```

  Expected: PASS; это восстанавливает актуальный baseline, не меняя production-код.

- [ ] **Step 3: Добавить новые unit-контракты поведения**

  В `App.test.tsx` добавить проверки:

  ```tsx
  it('closes the mobile menu with Escape and returns focus to the toggle', async () => {
    render(<App />);
    const user = userEvent.setup();
    const toggle = screen.getByRole('button', { name: 'Открыть меню' });
    await user.click(toggle);
    await user.keyboard('{Escape}');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).toHaveFocus();
  });

  it('shows the URL error before the optional brief and refocuses the URL input', async () => {
    render(<App />);
    const user = userEvent.setup();
    const input = screen.getAllByRole('textbox', { name: 'Ссылка на действующий сайт' })[0];
    await user.type(input, 'example.com');
    await user.click(screen.getAllByRole('button', { name: 'Получить бесплатную версию' })[0]);
    const alert = screen.getByRole('alert');
    const brief = screen.getAllByRole('textbox', { name: 'Пожелание к AI-виджету' })[0];
    expect(alert.compareDocumentPosition(brief) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(input).toHaveFocus();
  });
  ```

  В `ProductTour.test.tsx` добавить `matchMedia('(max-width: 767px)')` и ожидание `data-autoplay="false"` на mobile.

- [ ] **Step 4: Добавить Playwright helper-контракты**

  В `landing.spec.ts` добавить helpers:

  ```ts
  const MOBILE_PORTRAITS = [
    { width: 320, height: 568 },
    { width: 360, height: 800 },
    { width: 390, height: 844 },
    { width: 430, height: 932 },
  ] as const;

  async function expectContained(locator: Locator, viewportWidth: number) {
    const box = await locator.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(-1);
    expect(box!.x + box!.width).toBeLessThanOrEqual(viewportWidth + 1);
  }
  ```

  Добавить `@mobile` тест, который на каждой ширине проверяет containment `.hero-section__inner`, `.product-tour__scene`, `.case-heading`, активной `.case-panel`, `.capability-stage`, `.studio-layout`, `.faq-layout`, `.final-cta-content`; touch targets `a[href], button, input, textarea, summary`; шрифты смысловых mobile-селекторов; загрузку FORMA images; отсутствие autoplay; видимость standalone CTA. Добавить отдельный landscape проход для `568×320` и `844×390`.

- [ ] **Step 5: Подтвердить RED**

  Run:

  ```powershell
  npx vitest run src/App.test.tsx src/landing/ProductTour.test.tsx --reporter=verbose --maxWorkers=1 --pool=forks
  npx playwright test --project=mobile-390 --grep "mobile contracts" --reporter=line
  ```

  Expected failures: Escape не закрывает меню; URL input не возвращает focus; autoplay остаётся активным; case heading/panel и final visual выходят за локальные границы; часть текста меньше 12 px; standalone CTA скрыта.

### Task 2: Мобильные foundations, header, hero, формы и Final CTA

**Files:**
- Create: `frontend/src/mobile/index.css`
- Create: `frontend/src/mobile/foundations.css`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/landing/HeroSection.tsx`
- Modify: `frontend/src/shared/UrlComposer.tsx`

- [ ] **Step 1: Подключить изолированный mobile-слой после desktop CSS**

  `main.tsx` должен импортировать:

  ```ts
  import './styles.css';
  import './mobile/index.css';
  ```

  `mobile/index.css` на этом этапе содержит:

  ```css
  @import './foundations.css';
  ```

- [ ] **Step 2: Закрывать menu по Escape**

  В `HeroSection` при `menuOpen === true` подписаться на `window.keydown`; для `Escape` вызвать существующий `closeMobileMenu()`. Cleanup обязан удалять listener.

- [ ] **Step 3: Показать ошибку URL до brief и вернуть focus**

  Добавить `inputRef`, передать его в URL input, при invalid URL вызвать `inputRef.current?.focus({ preventScroll: true })` и `inputRef.current?.scrollIntoView({ block: 'center' })`. Перенести `.url-composer__error` сразу после `.url-composer__control`, сохранив `aria-describedby` и `role="alert"`.

- [ ] **Step 4: Реализовать foundations CSS только до 767 px**

  Правила обязаны обеспечить:

  ```css
  @media (max-width: 767px) {
    .site-header__logo { min-width: 44px; min-height: 44px; }
    .hero-section__inner { grid-template-columns: minmax(0, 1fr); }
    .hero-copy h1 { font-size: clamp(42px, 12.5vw, 56px); overflow-wrap: normal; }
    .url-composer input,
    .url-composer textarea { font-size: 16px; }
    .process-card__copy > span { font-size: 12px; }
    .final-cta-content { grid-template-columns: minmax(0, 1fr); }
    .final-cta-copy { order: 1; }
    .final-composer { order: 2; }
    .guarantee-row { order: 3; }
    .final-cta-visual { order: 4; }
    .final-site-card__motion { position: relative; inset: auto; width: 100%; }
    .final-site-card__label { left: 12px; right: auto; }
    .site-footer a { min-height: 44px; display: inline-flex; align-items: center; }
  }
  ```

  Hero scene должен стать компактным доказательным визуалом: спрятать второстепенные микроподписи, но сохранить сайт, три шага и готовый widget; смысловой текст не меньше 12 px. Final visual сделать последовательным сравниванием без отрицательных `left`, а widget полностью удержать внутри after-card.

- [ ] **Step 5: Подтвердить GREEN foundations**

  Run:

  ```powershell
  npx vitest run src/App.test.tsx --reporter=verbose --maxWorkers=1 --pool=forks
  npx playwright test --project=mobile-390 --grep "mobile contracts|landing mobile" --reporter=line
  ```

  Expected: menu, invalid form, hero и Final CTA contracts PASS; Product Tour и mid-page проверки могут оставаться RED до следующих задач.

### Task 3: Перекомпоновать Product Tour для телефона

**Files:**
- Create: `frontend/src/mobile/tour.css`
- Modify: `frontend/src/mobile/index.css`
- Modify: `frontend/src/landing/ProductTour.tsx`

- [ ] **Step 1: Отключить autoplay на mobile**

  Использовать существующий `useMediaQuery` либо маленький локальный hook для `(max-width: 767px)` и включать autoplay только при `!mobile`. Desktop 8-секундный цикл сохраняется.

- [ ] **Step 2: Подключить `tour.css` после foundations**

  ```css
  @import './foundations.css';
  @import './tour.css';
  ```

- [ ] **Step 3: Сделать текст и этапы читаемыми**

  До 767 px: summary/instructions/note не меньше 13/13/12 px; controls 44 px; `.product-tour__steps` превращается в помеченный `data-mobile-snap` horizontal rail с `overflow-x:auto`, `scroll-snap-type:x mandatory`; каждая кнопка имеет `flex:0 0 min(66vw, 190px)`, `min-height:56px`, `scroll-snap-align:start`, шрифты 11/13 px и без ellipsis. Стрелки остаются альтернативой swipe.

- [ ] **Step 4: Упростить три visual stage**

  - Intake: один крупный FORMA screenshot; dock складывается вертикально или оставляет только читаемую URL-строку, 12 px и send 44×44.
  - Studio: mobile grid становится одной колонкой; мелкая chat-sidebar скрывается, реальный `forma-widget-answer.webp` занимает основной preview.
  - Publish: site screenshot остаётся крупным; install code 12 px и `overflow-wrap:anywhere`; длинное повторное пояснение скрывается.
  - В standalone вернуть `.product-tour__cta` как полноширинную кнопку минимум 48 px; embedded CTA можно скрыть.
  - Header `/tour` получает читаемый graphite wordmark и return link 12 px.

- [ ] **Step 5: Подтвердить GREEN Product Tour**

  Run:

  ```powershell
  npx vitest run src/landing/ProductTour.test.tsx --reporter=verbose --maxWorkers=1 --pool=forks
  npx playwright test --project=mobile-390 --grep "product tour|mobile contracts" --reporter=line
  ```

  Expected: все три этапа, standalone CTA, 12 px typography, 44 px controls, loaded images и no-autoplay PASS.

### Task 4: Перекомпоновать Analysis, Case, Capabilities и Studio

**Files:**
- Create: `frontend/src/mobile/sections.css`
- Modify: `frontend/src/mobile/index.css`

- [ ] **Step 1: Подключить sections stylesheet последним**

  ```css
  @import './foundations.css';
  @import './tour.css';
  @import './sections.css';
  ```

- [ ] **Step 2: Упростить Analysis**

  На mobile скрыть lens/focus decorations; browser сделать полноширинным и статичным; четыре observation notes вывести обычной сеткой 2×2, при 320 px одной колонкой. Label 14 px, copy 12 px. Высоту сцены определяет поток, не фиксированные 590 px.

- [ ] **Step 3: Исправить Case**

  На mobile задать `.case-heading` и `.case-comparison` одной колонкой `minmax(0, 1fr)`, убрать minimum 390 px, скрыть повторный `.case-panel__label`, активную панель растянуть на всю ширину. Toggle остаётся 44 px; активный browser/widget intersection ratio не меньше 0.95.

- [ ] **Step 4: Сжать Capabilities без потери смысла**

  Перевести user question и answer из absolute в normal flow; typing скрыть; оставить одну suggestion. Шесть capabilities отобразить как компактный список строк, без больших одинаковых card shells. Label 15 px, supporting copy 12–13 px. Целевая высота секции на 390 px меньше 1450 px.

- [ ] **Step 5: Сделать Studio честным mobile preview**

  Checklist сложить тремя вертикальными строками; скрыть псевдокнопки toolbar; desktop-site crop убрать, оставить крупный widget preview. Supporting text минимум 12 px, CTA 50 px. FreeResult и FAQ менять только если contract обнаружит конкретный дефект.

- [ ] **Step 6: Подтвердить GREEN mid-page**

  Run:

  ```powershell
  npx vitest run src/landing/AnalysisSection.test.tsx src/landing/CaseCapabilitiesMotion.test.tsx src/landing/LandingVisualContracts.test.tsx --reporter=verbose --maxWorkers=1 --pool=forks
  npx playwright test --project=mobile-390 --grep "mobile contracts|landing mobile" --reporter=line
  ```

  Expected: локальный containment, typography и target contracts PASS на всех portrait widths.

### Task 5: Финальная мобильная матрица, доступность и публикация preview

**Files:**
- Modify: `frontend/e2e/landing.spec.ts` only if a contract needs a selector correction, not weaker thresholds
- Modify: `docs/product-journal/2026-08.md`
- Modify: `docs/telegram/content-backlog.md`
- Create: `docs/telegram/release-packets/2026-08-15-mobile-landing.md`
- Modify: `deploy/frontend-history/versions.json`
- Modify: `deploy/frontend-history/README.md`

- [ ] **Step 1: Run full static and unit verification**

  ```powershell
  npm run typecheck
  npm run lint
  npx vitest run src --reporter=dot --maxWorkers=1 --pool=forks
  npm run build
  ```

  Expected: zero failures and zero lint warnings.

- [ ] **Step 2: Run full browser suite and mobile axe**

  ```powershell
  npx playwright test --reporter=line
  ```

  Mobile axe states: base page, opened menu, invalid form, both Case states, opened FAQ, all three `/tour` steps. Reduced motion: no active infinite animations. Landscape `568×320` and `844×390`: hero/menu/forms remain usable and section containers stay within viewport.

- [ ] **Step 3: Capture evidence**

  Save full-page reduced-motion screenshots to `output/playwright/` for 320×568, 390×844, 430×932 and 844×390; visually inspect each. Add locator snapshots only for `#case-study` and `#final-cta`, not full-page golden files.

- [ ] **Step 4: Review and document**

  Update the required Russian product journal, release packet and content backlog with measured before/after facts: removed 5–11 px semantic text, fixed clipped Case, visible form error, mobile no-autoplay, standalone CTA, tested width matrix. Do not claim production deployment.

- [ ] **Step 5: Commit, push and publish an isolated archive version**

  Commit implementation to `codex/product-ui`, push the branch, register the exact UI commit as the next immutable frontend-history version, then deploy only through `scripts/deploy_frontend_history.sh` in `/opt/kaigo-previews/frontend-history-source`. Verify versioned landing/tour/assets HTTP 200 and confirm `https://kaigo.space/` plus `/studio/` hashes are unchanged.
