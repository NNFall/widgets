import AxeBuilder from '@axe-core/playwright';

import { expect, test } from './fixtures/builder';

async function expectNoHorizontalOverflow(page: import('@playwright/test').Page) {
  const overflow = await page.evaluate(() =>
    Math.max(
      document.documentElement.scrollWidth,
      document.body.scrollWidth,
    ) - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
}

test('Studio creates an owned project and renders the refreshed SaaS run @desktop', async ({ page, builderApi }) => {
  test.setTimeout(60_000);
  await page.goto('/studio?url=https%3A%2F%2Fexample.com');

  await expect(page.getByRole('heading', { name: 'Новый проект в Kaigo Studio' })).toBeVisible();
  await expect(page.getByLabel('Ссылка на сайт')).toHaveValue('https://example.com');
  await page.getByLabel('Пожелание к AI-виджету').fill('Говори простым языком и помогай выбрать услугу.');
  await page.getByRole('button', { name: 'Создать проект' }).click();

  await expect.poll(() => builderApi.requests.filter(({ method, pathname }) =>
    method === 'POST' && pathname === '/api/projects',
  ).length).toBe(1);
  const projectRequest = builderApi.requests.find(({ method, pathname }) =>
    method === 'POST' && pathname === '/api/projects',
  );
  expect(projectRequest?.headers['x-csrf-token']).toBe(builderApi.csrfToken);
  expect(projectRequest?.body).toEqual({
    url: 'https://example.com/',
    brief: 'Говори простым языком и помогай выбрать услугу.',
  });
  await expect.poll(() => new URL(page.url()).searchParams.get('project')).toBe(builderApi.projectId);

  await page.getByRole('button', { name: 'Создать AI-виджет' }).click();
  await expect.poll(() => builderApi.requests.filter(({ method, pathname }) =>
    method === 'POST' && pathname === `/api/projects/${builderApi.projectId}/runs`,
  ).length).toBe(1);
  const runRequest = builderApi.requests.find(({ method, pathname }) =>
    method === 'POST' && pathname === `/api/projects/${builderApi.projectId}/runs`,
  );
  expect(runRequest?.headers['x-csrf-token']).toBe(builderApi.csrfToken);
  expect(runRequest?.headers['idempotency-key']).toBe(`studio-${builderApi.projectId}`);
  expect(runRequest?.body).toEqual({ mode: 'express' });

  await expect.poll(() => builderApi.requests.some(({ method, pathname }) =>
    method === 'GET' && pathname === '/api/runs/run-created/events',
  )).toBe(true);
  await expect.poll(() => builderApi.requests.some(({ method, pathname }) =>
    method === 'GET' && pathname === '/api/runs/run-created',
  )).toBe(true);
  expect(builderApi.requests.some(({ pathname }) => pathname.includes('/builder/api/runs'))).toBe(false);

  await expect(page.locator('.studio-timeline .studio-event__meta strong').filter({
    hasText: 'Визуальная проверка пройдена',
  })).toBeVisible();
  await expect(page.locator('.studio-workspace__quality').getByText('Проверено', { exact: true })).toBeVisible();

  const frame = page.getByTitle('Предпросмотр AI-сотрудника Kaigo');
  await expect(frame).toHaveAttribute('sandbox', 'allow-scripts');
  await expect(frame).toHaveAttribute('referrerpolicy', 'no-referrer');
  await expect(frame).toHaveAttribute('src', /\/api\/runs\/run-created\/preview\/document\?revision=4&channel=/);
  await expect(page.frameLocator('iframe[title="Предпросмотр AI-сотрудника Kaigo"]').getByRole('heading', {
    name: 'AI-консультант',
  })).toBeVisible();
  await expect.poll(() => page.evaluate((key) => localStorage.getItem(key), `kaigo.saas.project.${builderApi.projectId}.idempotency-key`))
    .toBe(`studio-${builderApi.projectId}`);
  await expectNoHorizontalOverflow(page);

  await expect(page).toHaveScreenshot('studio-desktop.png', {
    animations: 'disabled',
    fullPage: true,
    maxDiffPixelRatio: 0.015,
    timeout: 30_000,
  });
});

test('Studio resumes the server-owned project with metrics and preview after reload @desktop', async ({ page, builderApi }) => {
  builderApi.seedRun('run-resume');
  await page.goto(`/studio?project=${builderApi.projectId}`);

  await expect(page.getByLabel('Ссылка на сайт')).toHaveValue('https://example.com');
  await expect(page.getByLabel('Пожелание к AI-сотруднику')).toHaveValue(
    'Уверенный консультант, который говорит простым языком.',
  );
  await expect(page.getByText('12 345', { exact: true })).toBeVisible();
  await expect(page.getByText('24,8 с', { exact: true })).toBeVisible();
  await expect(page.getByTitle('Предпросмотр AI-сотрудника Kaigo')).toHaveAttribute(
    'src',
    /\/api\/runs\/run-resume\/preview\/document\?revision=4&channel=/,
  );
  expect(await page.evaluate(() => localStorage.getItem('kaigo.builder.activeRun.v1'))).toBeNull();

  const projectReadsBeforeReload = builderApi.requests.filter(({ method, pathname }) =>
    method === 'GET' && pathname === `/api/projects/${builderApi.projectId}`,
  ).length;
  await page.reload();
  await expect(page.getByLabel('Ссылка на сайт')).toHaveValue('https://example.com');
  await expect(page.getByTitle('Предпросмотр AI-сотрудника Kaigo')).toBeVisible();
  await expect.poll(() => builderApi.requests.filter(({ method, pathname }) =>
    method === 'GET' && pathname === `/api/projects/${builderApi.projectId}`,
  ).length).toBeGreaterThan(projectReadsBeforeReload);
});

test('Studio owner cancels and safely retries a recoverable project run @desktop', async ({ page, builderApi }) => {
  const source = builderApi.seedRunningRun('run-recoverable');
  await page.goto(`/studio?project=${builderApi.projectId}`);

  await page.getByRole('button', { name: 'Отменить генерацию' }).click();
  await expect(page.getByText('Отмена запрошена — генерация остановится безопасно').first()).toBeVisible();
  await expect.poll(() => builderApi.requests.some(({ method, pathname }) =>
    method === 'POST' && pathname === `/api/runs/${source.id}/cancel`,
  )).toBe(true);
  const cancel = builderApi.requests.find(({ method, pathname }) =>
    method === 'POST' && pathname === `/api/runs/${source.id}/cancel`,
  );
  expect(cancel?.headers['x-csrf-token']).toBe(builderApi.csrfToken);

  await expect(page.getByRole('button', { name: 'Повторить запуск' })).toBeEnabled();
  await page.getByRole('button', { name: 'Повторить запуск' }).click();
  await expect.poll(() => builderApi.requests.some(({ method, pathname }) =>
    method === 'POST' && pathname === `/api/runs/${source.id}/retry`,
  )).toBe(true);
  const retry = builderApi.requests.find(({ method, pathname }) =>
    method === 'POST' && pathname === `/api/runs/${source.id}/retry`,
  );
  expect(retry?.headers['x-csrf-token']).toBe(builderApi.csrfToken);
  expect(retry?.headers['idempotency-key']).toBe(`studio-retry-${source.id}`);
  await expect(page.getByText('Визуальная проверка пройдена').first()).toBeVisible();
});

test('active subscription publishes the current verified artifact with a stable embed snippet @desktop', async ({ page, builderApi }) => {
  builderApi.seedRun('run-publish');
  builderApi.activateSubscription();
  await page.goto(`/studio?project=${builderApi.projectId}`);

  await expect(page.getByRole('heading', { name: 'Тариф активирован' })).toBeVisible();
  await page.getByLabel('Разрешённые домены').fill(
    'https://example.com\nhttps://shop.example.com',
  );
  await page.getByRole('button', { name: 'Опубликовать виджет' }).click();

  await expect(page.getByText(
    '<script src="https://widgets.kaigo.space/embed/stable-playwright-widget.js" async></script>',
  )).toBeVisible();
  const request = builderApi.requests.find(({ method, pathname }) =>
    method === 'POST' && pathname === `/api/projects/${builderApi.projectId}/publish`,
  );
  expect(request?.headers['x-csrf-token']).toBe(builderApi.csrfToken);
  expect(request?.body).toEqual({
    artifact_id: 'artifact-playwright-4',
    revision: 4,
    allowed_domains: ['https://example.com', 'https://shop.example.com'],
  });

  await page.reload();
  await expect(page.getByText(
    '<script src="https://widgets.kaigo.space/embed/stable-playwright-widget.js" async></script>',
  )).toBeVisible();
  await expect(page.getByLabel('Разрешённые домены')).toHaveValue(
    'https://example.com\nhttps://shop.example.com',
  );
});

test('Studio mobile restores a SaaS project, switches preview and has no overflow @mobile', async ({ page, builderApi }) => {
  test.setTimeout(60_000);
  builderApi.seedRun('run-mobile');
  await page.goto(`/studio?project=${builderApi.projectId}`);

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

test('Studio SaaS project is immediately usable with reduced motion @reduced', async ({ page, builderApi }) => {
  builderApi.seedRun('run-reduced');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(`/studio?project=${builderApi.projectId}`);

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

test('Studio SaaS project has no serious or critical accessibility violations @a11y', async ({ page, builderApi }) => {
  builderApi.seedRun('run-a11y');
  builderApi.activateSubscription();
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(`/studio?project=${builderApi.projectId}`);
  await expect(page.getByTitle('Предпросмотр AI-сотрудника Kaigo')).toBeVisible();

  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa'])
    .analyze();
  const blocking = results.violations.filter(({ impact }) =>
    impact === 'serious' || impact === 'critical',
  );
  expect(blocking).toEqual([]);
});
