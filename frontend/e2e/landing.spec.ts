import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Locator, type Page, type TestInfo } from '@playwright/test';

const EXACT_HERO = 'Через 10 минут вы сможете сказать: наш бизнес использует AI';

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(() =>
    Math.max(
      document.documentElement.scrollWidth,
      document.body.scrollWidth,
    ) - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
}

async function attachScreenshot(
  page: Page,
  testInfo: TestInfo,
  name: string,
  options: { fullPage?: boolean } = {},
) {
  const body = await page.screenshot({
    fullPage: options.fullPage,
  });
  await testInfo.attach(name, { body, contentType: 'image/png' });
}

async function revealLanding(page: Page) {
  const sections = page.locator('[data-landing-section]');
  const sectionCount = await sections.count();
  for (let index = 0; index < sectionCount; index += 1) {
    await sections.nth(index).scrollIntoViewIfNeeded();
    await page.waitForTimeout(80);
  }
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
}

async function expectMinimumTarget(locator: Locator, minimum = 44) {
  const box = await locator.boundingBox();
  expect(box, 'interactive target must have a rendered box').not.toBeNull();
  expect(box?.width ?? 0).toBeGreaterThanOrEqual(minimum);
  expect(box?.height ?? 0).toBeGreaterThanOrEqual(minimum);
}

async function expectCompactFirstScreen(page: Page) {
  const viewport = page.viewportSize();
  expect(viewport, 'compact project must provide a viewport').not.toBeNull();
  const viewportLabel = `${viewport?.width}x${viewport?.height}`;
  const scene = page.getByTestId('hero-scene');
  const finalWidget = page.locator('[data-testid="widget-preview"][data-visible="true"]');
  const widgetLabel = page.locator('.hero-browser-stage__widget-label');
  const heroComposer = page.locator('.hero-copy .url-composer');
  const processCards = page.locator('[data-testid="process-card"][data-visible="true"]');
  await expect(widgetLabel).toBeVisible();
  await expect(heroComposer).toBeVisible();
  await expect(processCards).toHaveCount(3);

  const firstScreenElements: Array<[string, Locator]> = [
    ['hero section', page.locator('.hero-section')],
    ['hero copy', page.locator('.hero-copy')],
    ['hero scene', scene],
    ['URL composer', heroComposer],
    ['widget label', widgetLabel],
    ['final widget', finalWidget],
  ];
  for (let index = 0; index < 3; index += 1) {
    firstScreenElements.push([`process card ${index + 1}`, processCards.nth(index)]);
  }

  const viewportTolerance = 1;
  for (const [name, locator] of firstScreenElements) {
    await expect(locator, `${name} must be visible in the completed hero at ${viewportLabel}`).toBeVisible();
    const bounds = await locator.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return {
        left: rect.left,
        top: rect.top,
        right: rect.right,
        bottom: rect.bottom,
        viewportWidth: window.innerWidth,
        viewportHeight: window.innerHeight,
      };
    });
    expect.soft(bounds.left, `${name} must not protrude past the left viewport edge at ${viewportLabel}`)
      .toBeGreaterThanOrEqual(-viewportTolerance);
    expect.soft(bounds.top, `${name} must not protrude past the top viewport edge at ${viewportLabel}`)
      .toBeGreaterThanOrEqual(-viewportTolerance);
    expect.soft(bounds.right, `${name} must not protrude past the right viewport edge at ${viewportLabel}`)
      .toBeLessThanOrEqual(bounds.viewportWidth + viewportTolerance);
    expect.soft(bounds.bottom, `${name} must not protrude past the bottom viewport edge at ${viewportLabel}`)
      .toBeLessThanOrEqual(bounds.viewportHeight + viewportTolerance);
  }
  await expectNoHorizontalOverflow(page);

  const header = page.locator('.site-header');
  const headerBox = await header.boundingBox();
  expect(headerBox, `site header must have a rendered box at ${viewportLabel}`).not.toBeNull();
  expect.soft(headerBox?.height ?? Number.POSITIVE_INFINITY, `header height at ${viewportLabel}`)
    .toBeLessThanOrEqual(96);

  const heroHeadingFontSize = await page.locator('.hero-copy h1').evaluate((element) =>
    Number.parseFloat(getComputedStyle(element).fontSize),
  );
  expect.soft(heroHeadingFontSize, `hero heading font size at ${viewportLabel}`).toBeLessThanOrEqual(50);
}

test('landing compact desktop fits the first screen and exposes the brand @compact', async ({ page }) => {
  test.setTimeout(90_000);
  await page.addInitScript(() => {
    type HeroMotionDiagnostic = {
      sourceAt: number | null;
      completeAt: number | null;
    };

    const diagnostic: HeroMotionDiagnostic = { sourceAt: null, completeAt: null };
    (window as Window & { __kaigoHeroMotionTiming?: HeroMotionDiagnostic }).__kaigoHeroMotionTiming = diagnostic;

    const sceneSelector = '[data-testid="hero-scene"]';
    const recordPhase = (scene: Element) => {
      const phase = scene.getAttribute('data-motion-phase');
      if (phase === 'source' && diagnostic.sourceAt === null) {
        diagnostic.sourceAt = performance.now();
      }
      if (phase === 'complete' && diagnostic.completeAt === null) {
        diagnostic.completeAt = performance.now();
      }
    };
    const inspectNode = (node: Node) => {
      if (!(node instanceof Element)) return;
      if (node.matches(sceneSelector)) recordPhase(node);
      node.querySelectorAll(sceneSelector).forEach(recordPhase);
    };

    const observer = new MutationObserver((records) => {
      for (const record of records) {
        if (record.type === 'attributes') {
          inspectNode(record.target);
          continue;
        }
        record.addedNodes.forEach(inspectNode);
      }
    });
    observer.observe(document, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ['data-motion-phase'],
    });
    if (document.documentElement) inspectNode(document.documentElement);
  });

  await page.goto('/');

  const scene = page.getByTestId('hero-scene');
  await expect(scene).toHaveAttribute('data-motion-phase', 'complete', { timeout: 20_000 });
  const motionTiming = await page.evaluate(() => (
    (window as Window & {
      __kaigoHeroMotionTiming?: { sourceAt: number | null; completeAt: number | null };
    }).__kaigoHeroMotionTiming
  ));
  expect(motionTiming?.sourceAt, 'observer must capture the first source phase').toEqual(expect.any(Number));
  expect(motionTiming?.completeAt, 'observer must capture the first complete phase').toEqual(expect.any(Number));
  const motionDurationMs = (motionTiming?.completeAt ?? Number.POSITIVE_INFINITY)
    - (motionTiming?.sourceAt ?? Number.NEGATIVE_INFINITY);
  expect(motionDurationMs, 'complete phase must follow the source phase').toBeGreaterThanOrEqual(0);
  expect(motionDurationMs, 'hero motion from source to complete must finish within 12 seconds')
    .toBeLessThanOrEqual(12_000);

  await expectCompactFirstScreen(page);
  await page.setViewportSize({ width: 1_366, height: 768 });
  await page.evaluate(() => new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  }));
  await expectCompactFirstScreen(page);

  await expect.soft(page).toHaveTitle('Kaigo — AI в вашем бизнесе за 10 минут');
  await expect.soft(page.locator('link[rel="icon"]')).toHaveAttribute('href', '/favicon.svg');
});

test('landing desktop completes the hero story without overflow @desktop', async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: EXACT_HERO })).toBeVisible();
  await expect(page.getByTestId('hero-scene')).toHaveAttribute('data-motion-phase', 'source');

  await attachScreenshot(page, testInfo, 'hero-source-1920.png');
  await expect(page.getByTestId('hero-scene')).toHaveAttribute('data-motion-phase', 'scanning', {
    timeout: 3_000,
  });
  await attachScreenshot(page, testInfo, 'hero-scan-1920.png');

  await expect(page.getByTestId('hero-scene')).toHaveAttribute('data-motion-phase', 'complete', {
    timeout: 12_000,
  });
  await expect(page.getByTestId('process-card')).toHaveCount(3);
  await expect(page.locator('[data-testid="widget-preview"][data-visible="true"]')).toBeVisible();
  await expectNoHorizontalOverflow(page);

  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect(page.locator('.hero-section')).toHaveScreenshot('hero-complete-1920.png', {
    animations: 'disabled',
    maxDiffPixelRatio: 0.015,
  });
  await revealLanding(page);
  await expect(page).toHaveScreenshot('landing-full-1920.png', {
    animations: 'disabled',
    fullPage: true,
    maxDiffPixelRatio: 0.015,
    timeout: 45_000,
  });
});

test('scanner travels continuously across card reveals @desktop', async ({ page }) => {
  await page.goto('/');

  const scene = page.getByTestId('hero-scene');
  const scanner = page.getByTestId('hero-scanner');

  await expect(scene).toHaveAttribute('data-visible-cards', '1', { timeout: 15_000 });
  const firstScan = await scanner.evaluate((element) => {
    const styles = getComputedStyle(element);
    return {
      animationName: styles.animationName,
      animationPlayState: styles.animationPlayState,
      y: element.getBoundingClientRect().y,
    };
  });
  expect(firstScan.animationName).toContain('hero-scanner-sweep');
  expect(firstScan.animationPlayState).toBe('running');

  await expect(scene).toHaveAttribute('data-visible-cards', '2', { timeout: 3_000 });
  const secondScan = await scanner.evaluate((element) => {
    const styles = getComputedStyle(element);
    return {
      animationName: styles.animationName,
      y: element.getBoundingClientRect().y,
    };
  });

  expect(secondScan.animationName).toBe(firstScan.animationName);
  expect(secondScan.y - firstScan.y).toBeGreaterThan(50);
});

test('landing navigation, composer, case toggle and FAQ are functional @desktop', async ({ page }) => {
  await page.goto('/');

  await page.getByRole('navigation', { name: 'Основная навигация' })
    .getByRole('link', { name: 'Кейсы' })
    .click();
  await expect(page).toHaveURL(/#case-study$/);
  await expect(page.getByRole('heading', { name: /Один и тот же сайт/ })).toBeVisible();

  const faqButton = page.getByRole('button', { name: 'Сколько времени занимает создание?' });
  await faqButton.focus();
  await expect(faqButton).toBeFocused();
  await faqButton.press('Enter');
  await expect(faqButton).toHaveAttribute('aria-expanded', 'true');
  await expect(page.getByText(/Первую версию AI-виджета/)).toBeVisible();

  await page.getByLabel('Ссылка на действующий сайт').first().fill('https://example.com');
  await page.getByRole('button', { name: 'Создать AI-виджет' }).first().click();
  await expect(page).toHaveURL(/\/studio\?url=https%3A%2F%2Fexample\.com$/);
  await expect(page.getByRole('heading', { name: 'Студия Kaigo' })).toBeVisible();
});

test('landing mobile preserves content order, menu, controls and comparison @mobile', async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: EXACT_HERO })).toBeVisible();
  await expectNoHorizontalOverflow(page);

  const copyBeforeScene = await page.evaluate(() => {
    const copy = document.querySelector('.hero-copy');
    const scene = document.querySelector('.hero-scene');
    return Boolean(copy && scene && (copy.compareDocumentPosition(scene) & Node.DOCUMENT_POSITION_FOLLOWING));
  });
  expect(copyBeforeScene).toBe(true);

  const menuButton = page.getByRole('button', { name: 'Открыть меню' });
  await expectMinimumTarget(menuButton);
  await menuButton.click();
  const mobileNavigation = page.getByRole('navigation', { name: 'Мобильная навигация' });
  await expect(mobileNavigation).toBeVisible();
  await mobileNavigation.getByRole('link', { name: 'Продукт' }).click();
  await expect(page).toHaveURL(/#product$/);
  await expect(page.getByRole('button', { name: 'Открыть меню' })).toBeFocused();

  await expectMinimumTarget(page.getByRole('button', { name: 'Создать AI-виджет' }).first());

  const caseSection = page.locator('#case-study');
  await caseSection.scrollIntoViewIfNeeded();
  const before = caseSection.getByRole('button', { name: 'До', exact: true });
  const after = caseSection.getByRole('button', { name: 'После', exact: true });
  await expect(after).toHaveAttribute('aria-pressed', 'true');
  await before.click();
  await expect(before).toHaveAttribute('aria-pressed', 'true');
  await expect(after).toHaveAttribute('aria-pressed', 'false');
  await expectNoHorizontalOverflow(page);
  const undersizedButtons = await page.locator('button:visible').evaluateAll((buttons) =>
    buttons.flatMap((button) => {
      const box = button.getBoundingClientRect();
      if (box.width >= 44 && box.height >= 44) return [];
      return [{
        name: button.getAttribute('aria-label') || button.textContent?.trim() || button.className,
        width: Math.round(box.width * 10) / 10,
        height: Math.round(box.height * 10) / 10,
      }];
    }),
  );
  expect(undersizedButtons).toEqual([]);

  await revealLanding(page);
  await expect(page).toHaveScreenshot('landing-full-390.png', {
    animations: 'disabled',
    fullPage: true,
    maxDiffPixelRatio: 0.02,
    timeout: 45_000,
  });
  await attachScreenshot(page, testInfo, 'landing-mobile-390.png', { fullPage: true });
});

test('landing reaches the final hero state immediately with reduced motion @reduced', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await expect(page.getByTestId('hero-scene')).toHaveAttribute('data-motion-phase', 'complete');
  await expect(page.getByTestId('process-card')).toHaveCount(3);
  await expect(page.locator('[data-testid="widget-preview"][data-visible="true"]')).toBeVisible();

  const infiniteAnimations = await page.evaluate(() =>
    document.getAnimations().filter((animation) => {
      const timing = animation.effect?.getComputedTiming();
      return animation.playState === 'running' && timing?.iterations === Number.POSITIVE_INFINITY;
    }).length,
  );
  expect(infiniteAnimations).toBe(0);
});

test('landing has no serious or critical accessibility violations @a11y', async ({ page }) => {
  test.setTimeout(90_000);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await revealLanding(page);
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa'])
    .analyze();
  const blocking = results.violations.filter(({ impact }) =>
    impact === 'serious' || impact === 'critical',
  );
  expect(blocking).toEqual([]);
});
