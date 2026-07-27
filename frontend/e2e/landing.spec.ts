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
  for (let index = 0; index < await sections.count(); index += 1) {
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
    timeout: 7_000,
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

  await expect(scene).toHaveAttribute('data-visible-cards', '1', { timeout: 4_000 });
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
