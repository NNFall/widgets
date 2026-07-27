import AxeBuilder from '@axe-core/playwright';

import { expect, test } from './fixtures/builder';

const ACTIVE_RUN_STORAGE_KEY = 'kaigo.builder.activeRun.v1';

async function expectNoHorizontalOverflow(page: import('@playwright/test').Page) {
  const overflow = await page.evaluate(() =>
    Math.max(
      document.documentElement.scrollWidth,
      document.body.scrollWidth,
    ) - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
}

test('Studio creates a run and renders its timeline and sandboxed preview @desktop', async ({ page, builderApi }) => {
  test.setTimeout(60_000);
  await page.goto('/studio');
  await page.getByLabel('Ссылка на сайт').fill('https://example.com');
  await page.getByLabel('Пожелание к AI-сотруднику').fill('Говори простым языком и помогай выбрать услугу.');
  await page.getByRole('button', { name: 'Создать AI-виджет' }).click();

  await expect.poll(() => builderApi.requests.filter(({ method, pathname }) =>
    method === 'POST' && pathname.endsWith('/builder/api/runs'),
  ).length).toBe(1);
  await expect(page.locator('.studio-timeline .studio-event__meta strong').filter({
    hasText: 'Запуск создан',
  })).toBeVisible();
  await expect(page.locator('.studio-timeline .studio-event__meta strong').filter({
    hasText: 'Визуальная проверка пройдена',
  })).toBeVisible();
  await expect(page.locator('.studio-workspace__quality').getByText('Проверено', { exact: true })).toBeVisible();

  const frame = page.getByTitle('Предпросмотр AI-сотрудника Kaigo');
  await expect(frame).toHaveAttribute('sandbox', 'allow-scripts');
  await expect(frame).toHaveAttribute('referrerpolicy', 'no-referrer');
  await expect(frame).toHaveAttribute('src', /\/builder\/api\/runs\/run-created\/preview\?revision=4&channel=/);
  await expect(page.frameLocator('iframe[title="Предпросмотр AI-сотрудника Kaigo"]').getByRole('heading', {
    name: 'AI-консультант',
  })).toBeVisible();
  await expect.poll(() => page.evaluate((key) => localStorage.getItem(key), ACTIVE_RUN_STORAGE_KEY))
    .toBe('run-created');
  await expectNoHorizontalOverflow(page);

  await expect(page).toHaveScreenshot('studio-desktop.png', {
    animations: 'disabled',
    fullPage: true,
    maxDiffPixelRatio: 0.015,
    timeout: 30_000,
  });
});

test('Studio resumes a stored run with form values, metrics and preview @desktop', async ({ page, builderApi }) => {
  builderApi.seedRun('run-resume');
  await page.addInitScript(({ key }) => localStorage.setItem(key, 'run-resume'), {
    key: ACTIVE_RUN_STORAGE_KEY,
  });
  await page.goto('/studio');

  await expect(page.getByLabel('Ссылка на сайт')).toHaveValue('https://example.com');
  await expect(page.getByLabel('Пожелание к AI-сотруднику')).toHaveValue(
    'Уверенный консультант, который говорит простым языком.',
  );
  await expect(page.getByText('12 345', { exact: true })).toBeVisible();
  await expect(page.getByText('24,8 с', { exact: true })).toBeVisible();
  await expect(page.getByTitle('Предпросмотр AI-сотрудника Kaigo')).toHaveAttribute(
    'src',
    /\/run-resume\/preview\?revision=4&channel=/,
  );

  await page.reload();
  await expect(page.getByLabel('Ссылка на сайт')).toHaveValue('https://example.com');
  await expect(page.getByTitle('Предпросмотр AI-сотрудника Kaigo')).toBeVisible();
});

test('Studio mobile restores a run, switches preview and has no overflow @mobile', async ({ page, builderApi }) => {
  test.setTimeout(60_000);
  builderApi.seedRun('run-mobile');
  await page.addInitScript(({ key }) => localStorage.setItem(key, 'run-mobile'), {
    key: ACTIVE_RUN_STORAGE_KEY,
  });
  await page.goto('/studio');

  await expect(page.getByRole('heading', { name: 'Студия Kaigo' })).toBeVisible();
  await expect(page.getByTestId('studio-preview-canvas')).toHaveAttribute('data-viewport', 'desktop');
  await page.getByRole('button', { name: 'Mobile' }).click();
  await expect(page.getByRole('button', { name: 'Mobile' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByTestId('studio-preview-canvas')).toHaveAttribute('data-viewport', 'mobile');
  await expect(page.getByTitle('Предпросмотр AI-сотрудника Kaigo')).toHaveAttribute('sandbox', 'allow-scripts');
  await expectNoHorizontalOverflow(page);

  await expect(page).toHaveScreenshot('studio-mobile.png', {
    animations: 'disabled',
    fullPage: true,
    maxDiffPixelRatio: 0.02,
    timeout: 30_000,
  });
});

test('Studio is immediately usable with reduced motion @reduced', async ({ page, builderApi }) => {
  builderApi.seedRun('run-reduced');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.addInitScript(({ key }) => localStorage.setItem(key, 'run-reduced'), {
    key: ACTIVE_RUN_STORAGE_KEY,
  });
  await page.goto('/studio');

  await expect(page.getByTitle('Предпросмотр AI-сотрудника Kaigo')).toBeVisible();
  const reducedMotionState = await page.evaluate(() => {
    const sessionStatus = document.querySelector('.studio-header__session strong');
    const pseudoStyle = sessionStatus ? getComputedStyle(sessionStatus, '::before') : null;
    const infiniteAnimations = document.getAnimations().filter((animation) => {
      const timing = animation.effect?.getComputedTiming();
      return animation.playState === 'running' && timing?.iterations === Number.POSITIVE_INFINITY;
    }).map((animation) => {
      const effect = animation.effect as KeyframeEffect | null;
      const target = effect?.target as Element | null;
      return {
        animationName: animation instanceof CSSAnimation ? animation.animationName : animation.constructor.name,
        target: target?.className || target?.tagName || 'unknown',
      };
    });
    return {
      mediaMatches: matchMedia('(prefers-reduced-motion: reduce)').matches,
      pseudoAnimation: {
        animationName: pseudoStyle?.animationName ?? null,
        duration: pseudoStyle?.animationDuration ?? null,
        iterationCount: pseudoStyle?.animationIterationCount ?? null,
      },
      infiniteAnimations,
    };
  });
  expect(reducedMotionState).toEqual({
    mediaMatches: true,
    pseudoAnimation: {
      animationName: 'none',
      duration: '0s',
      iterationCount: '1',
    },
    infiniteAnimations: [],
  });
});

test('Studio has no serious or critical accessibility violations @a11y', async ({ page, builderApi }) => {
  builderApi.seedRun('run-a11y');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.addInitScript(({ key }) => localStorage.setItem(key, 'run-a11y'), {
    key: ACTIVE_RUN_STORAGE_KEY,
  });
  await page.goto('/studio');
  await expect(page.getByTitle('Предпросмотр AI-сотрудника Kaigo')).toBeVisible();

  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa'])
    .analyze();
  const blocking = results.violations.filter(({ impact }) =>
    impact === 'serious' || impact === 'critical',
  );
  expect(blocking).toEqual([]);
});
