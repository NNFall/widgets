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

async function expectRuntimeOpensAndCloses(page: import('@playwright/test').Page) {
  const frame = page.getByTitle('Предпросмотр консультанта Kaigo');
  const frameHandle = await frame.elementHandle();
  const contentFrame = await frameHandle?.contentFrame();
  expect(contentFrame).not.toBeNull();
  const launcher = contentFrame!.getByRole('button', { name: 'Открыть чат' });
  const panel = contentFrame!.getByRole('dialog', { name: 'Помощник Kaigo' });
  const close = contentFrame!.getByRole('button', { name: 'Закрыть чат' });

  await frame.evaluate((element) => element.scrollIntoView({ block: 'center', inline: 'center' }));
  await expect(launcher).toBeVisible();
  await expect(panel).toBeHidden();
  await launcher.evaluate((element) => (element as HTMLButtonElement).click());
  const runtimeState = await contentFrame!.locator('.widget-root').getAttribute('data-state');
  expect(runtimeState).toBe('open');
  await expect(panel).toBeVisible();
  await expect(close).toBeVisible();

  const [frameBox, panelBox, closeBox] = await Promise.all([
    frame.boundingBox(),
    panel.boundingBox(),
    close.boundingBox(),
  ]);
  expect(frameBox).not.toBeNull();
  expect(panelBox).not.toBeNull();
  expect(closeBox).not.toBeNull();
  expect(panelBox!.x).toBeGreaterThanOrEqual(frameBox!.x);
  expect(panelBox!.y).toBeGreaterThanOrEqual(frameBox!.y);
  expect(panelBox!.x + panelBox!.width).toBeLessThanOrEqual(frameBox!.x + frameBox!.width);
  expect(panelBox!.y + panelBox!.height).toBeLessThanOrEqual(frameBox!.y + frameBox!.height);
  expect(closeBox!.x).toBeGreaterThanOrEqual(frameBox!.x);
  expect(closeBox!.y).toBeGreaterThanOrEqual(frameBox!.y);
  expect(closeBox!.x + closeBox!.width).toBeLessThanOrEqual(frameBox!.x + frameBox!.width);
  expect(closeBox!.y + closeBox!.height).toBeLessThanOrEqual(frameBox!.y + frameBox!.height);
  const closeCssSize = await close.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return { width: Math.round(rect.width), height: Math.round(rect.height) };
  });
  expect(closeCssSize).toEqual({ width: 44, height: 44 });

  await close.click();
  await expect(panel).toBeHidden();
  await expect(launcher).toBeVisible();
}

test('Studio creates an owned project and renders the refreshed SaaS run @desktop', async ({ page, builderApi }) => {
  test.setTimeout(60_000);
  await page.goto('/studio?url=https%3A%2F%2Fexample.com');

  await expect(page.getByRole('heading', { name: 'Мои виджеты' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Создайте новый виджет' })).toBeVisible();
  await expect(page.getByText('example.com').first()).toBeVisible();
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

  await expect(page.getByRole('heading', { name: 'Создайте первый AI-виджет' })).toBeVisible();
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

  await expect(page.getByRole('heading', { name: 'Виджет готов' })).toBeVisible();
  await expect(page.getByRole('list', { name: 'Этапы создания виджета' }).getByRole('listitem')).toHaveCount(10);
  const technicalDetails = page.locator('details.studio-technical');
  await expect(technicalDetails).not.toHaveAttribute('open');
  await technicalDetails.click();
  await expect(technicalDetails.getByText('Виджет прошёл визуальную проверку')).toBeVisible();
  await technicalDetails.click();
  const conversation = page.getByRole('complementary', { name: 'Чат с Kaigo' });
  const previewRegion = page.getByRole('region', { name: 'Предпросмотр виджета' });
  await expect(conversation).toBeVisible();
  await expect(previewRegion).toBeVisible();
  await expect(previewRegion).toContainText('Версия 2');
  await expect(previewRegion).toContainText('Проверено');
  await expect(page.getByLabel('Что изменить в виджете?')).toBeVisible();

  const [workbenchBox, conversationBox] = await Promise.all([
    page.getByRole('main', { name: 'Рабочая студия' }).boundingBox(),
    conversation.boundingBox(),
  ]);
  expect(workbenchBox).not.toBeNull();
  expect(conversationBox).not.toBeNull();
  if (!workbenchBox || !conversationBox) throw new Error('Studio workbench is not visible');
  expect(conversationBox.width / workbenchBox.width).toBeGreaterThan(0.42);

  const frame = page.getByTitle('Предпросмотр консультанта Kaigo');
  await expect(frame).toHaveAttribute('sandbox', 'allow-scripts');
  await expect(frame).toHaveAttribute('referrerpolicy', 'no-referrer');
  await expect(frame).toHaveAttribute('src', /\/api\/runs\/run-created\/preview\/document\?revision=4&channel=/);
  await expect(page.frameLocator('iframe[title="Предпросмотр консультанта Kaigo"]').getByRole('button', {
    name: 'Открыть чат',
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

test('Studio resumes the server-owned project with friendly status and preview after reload @desktop', async ({ page, builderApi }) => {
  builderApi.seedRun('run-resume');
  await page.goto(`/studio?project=${builderApi.projectId}`);

  const projectContext = page.locator('details.studio-conversation__context');
  await expect(projectContext).not.toHaveAttribute('open');
  await projectContext.getByText('Контекст проекта').click();
  await expect(projectContext.getByText('https://example.com', { exact: true })).toBeVisible();
  await expect(projectContext.getByText(
    'Уверенный консультант, который говорит простым языком.',
  )).toBeVisible();
  await expect(page.getByText('example.com').first()).toBeVisible();
  await expect(page.getByRole('region', { name: 'Предпросмотр виджета' })).toContainText('Версия 2');
  await expect(page.getByText('12 345', { exact: true })).not.toBeVisible();
  await expect(page.getByText('24,8 с', { exact: true })).not.toBeVisible();
  await expect(page.getByTitle('Предпросмотр консультанта Kaigo')).toHaveAttribute(
    'src',
    /\/api\/runs\/run-resume\/preview\/document\?revision=4&channel=/,
  );
  expect(await page.evaluate(() => localStorage.getItem('kaigo.builder.activeRun.v1'))).toBeNull();

  const projectReadsBeforeReload = builderApi.requests.filter(({ method, pathname }) =>
    method === 'GET' && pathname === `/api/projects/${builderApi.projectId}`,
  ).length;
  await page.reload();
  await expect(page.locator('details.studio-conversation__context')).not.toHaveAttribute('open');
  await expect(page.getByTitle('Предпросмотр консультанта Kaigo')).toBeVisible();
  await expect(page.getByRole('region', { name: 'Предпросмотр виджета' })).toContainText('Версия 2');
  await expect.poll(() => builderApi.requests.filter(({ method, pathname }) =>
    method === 'GET' && pathname === `/api/projects/${builderApi.projectId}`,
  ).length).toBeGreaterThan(projectReadsBeforeReload);
});

test('Studio gives generated widgets audit-equivalent desktop and mobile viewports @desktop', async ({ page, builderApi }) => {
  builderApi.seedRun('run-preview-viewport');
  await page.goto(`/studio?project=${builderApi.projectId}`);

  const frame = page.getByTitle('Предпросмотр консультанта Kaigo');
  await expect(frame).toBeVisible();
  await expect(frame).toHaveAttribute('sandbox', 'allow-scripts');

  const desktopBox = await frame.boundingBox();
  expect(desktopBox).not.toBeNull();
  expect(desktopBox!.height).toBeGreaterThanOrEqual(620);
  await expectRuntimeOpensAndCloses(page);

  await page.getByRole('button', { name: 'На телефоне' }).click();
  await expect(page.locator('.studio-preview__device')).toHaveAttribute('data-viewport', 'mobile');
  await expect.poll(async () => {
    return page.frameLocator('iframe[title="Предпросмотр консультанта Kaigo"]')
      .locator('body')
      .evaluate(() => ({ width: window.innerWidth, height: window.innerHeight }));
  }).toEqual({ width: 390, height: 844 });
  await expect.poll(async () => {
    const [canvasBox, box] = await Promise.all([
      page.getByTestId('studio-preview-canvas').boundingBox(),
      frame.boundingBox(),
    ]);
    return Boolean(canvasBox && box
      && box.x >= canvasBox.x
      && box.y >= canvasBox.y
      && box.x + box.width <= canvasBox.x + canvasBox.width + 1
      && box.y + box.height <= canvasBox.y + canvasBox.height + 1);
  }).toBe(true);
  await expectRuntimeOpensAndCloses(page);
});

test('Studio owner cancels and safely retries a recoverable project run @desktop', async ({ page, builderApi }) => {
  const source = builderApi.seedRunningRun('run-recoverable');
  await page.goto(`/studio?project=${builderApi.projectId}`);

  await page.getByRole('button', { name: 'Отменить генерацию' }).click();
  await expect(page.getByRole('heading', { name: 'Создание остановлено' })).toBeVisible();
  await expect(page.getByText('Работу можно запустить снова.')).toBeVisible();
  await expect(page.getByRole('status')).toHaveCount(0);
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
  const retryDetails = page.locator('details.studio-technical');
  await retryDetails.click();
  await expect(retryDetails.getByText('Виджет прошёл визуальную проверку')).toBeVisible();
});

test('active subscription publishes the current verified artifact with a stable embed snippet @desktop @mobile', async ({ page, builderApi }) => {
  builderApi.seedRun('run-publish');
  builderApi.activateSubscription();
  await page.goto(`/studio?project=${builderApi.projectId}`);

  await page.getByRole('button', { name: 'Открыть тариф и лимиты' }).click();
  const accountDrawer = page.getByRole('dialog', { name: 'Тариф и лимиты' });
  await expect(accountDrawer.getByText('Starter')).toBeVisible();
  await expect(accountDrawer.getByText(/750\s000/)).toBeVisible();
  await expect(accountDrawer.getByText('Автопродление включено')).toBeVisible();
  expect(await accountDrawer.evaluate((element) => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1);
  await accountDrawer.getByRole('button', { name: 'Закрыть панель' }).click();

  await page.getByRole('button', { name: 'Открыть публикацию' }).click();
  await expect(page.getByRole('heading', { name: 'Всё готово к публикации' })).toBeVisible();
  await page.getByLabel('На каких сайтах разрешить виджет').fill(
    'https://example.com\nhttps://shop.example.com',
  );
  await page.getByRole('button', { name: 'Опубликовать виджет' }).click();

  await expect(page.getByRole('heading', { name: 'Виджет опубликован' })).toBeVisible();
  const embedSnippet = page.getByText(
    '<script src="https://widgets.kaigo.space/embed/stable-playwright-widget.js" async></script>',
  );
  await expect(embedSnippet).not.toBeVisible();
  await page.getByText('Код для разработчика').click();
  await expect(embedSnippet).toBeVisible();
  const publicationDialog = page.getByRole('dialog', { name: 'Публикация виджета' });
  const modalBox = await publicationDialog.boundingBox();
  const viewport = page.viewportSize();
  if (viewport && viewport.width <= 760) {
    expect(modalBox?.width ?? 0).toBeLessThanOrEqual(viewport.width);
  } else {
    expect(modalBox?.width ?? 0).toBeGreaterThan(900);
  }
  const drawerOverflow = await publicationDialog.evaluate((element) =>
    element.scrollWidth - element.clientWidth,
  );
  expect(drawerOverflow).toBeLessThanOrEqual(1);
  const request = builderApi.requests.find(({ method, pathname }) =>
    method === 'POST' && pathname === `/api/projects/${builderApi.projectId}/publish`,
  );
  expect(request?.headers['x-csrf-token']).toBe(builderApi.csrfToken);
  expect(request?.body).toEqual({
    project_version_id: 'version-playwright-2',
    expected_active_release_id: null,
    allowed_domains: ['https://example.com', 'https://shop.example.com'],
  });

  await page.reload();
  const restoredSnippet = page.getByText(
    '<script src="https://widgets.kaigo.space/embed/stable-playwright-widget.js" async></script>',
  );
  await expect(restoredSnippet).not.toBeVisible();
  await page.getByRole('button', { name: 'Открыть публикацию' }).click();
  await expect(page.getByLabel('На каких сайтах разрешить виджет')).toHaveValue(
    'https://example.com\nhttps://shop.example.com',
  );
});

test('publication access offer stays readable in the publication modal @desktop @mobile', async ({ page, builderApi }) => {
  await page.route('**/api/billing/offer**', (route) => route.fulfill({
    status: 200,
    json: {
      founder: {
        eligible: true,
        reason: null,
        remaining: 14,
        capacity: 20,
        period_days: 14,
        generation_tokens: 1_500_000,
      },
      plans: [
        {
          code: 'starter_intro_15d',
          title: 'Kaigo Starter, первые 15 дней',
          amount_minor: 50_000,
          currency: 'RUB',
          period_days: 15,
          generation_tokens: 500_000,
          renewal: null,
        },
        {
          code: 'starter_monthly',
          title: 'Kaigo Starter, 1 месяц',
          amount_minor: 200_000,
          currency: 'RUB',
          period_days: 30,
          generation_tokens: 1_000_000,
          renewal: null,
        },
        {
          code: 'starter_quarterly',
          title: 'Kaigo Starter, 3 месяца',
          amount_minor: 500_000,
          currency: 'RUB',
          period_days: 90,
          generation_tokens: 3_000_000,
          renewal: null,
        },
      ],
    },
  }));
  builderApi.seedRun('run-publication-offer');
  await page.goto(`/studio?project=${builderApi.projectId}`);

  await page.getByRole('button', { name: 'Открыть публикацию' }).click();
  const publicationDialog = page.getByRole('dialog', { name: 'Публикация виджета' });
  await publicationDialog.getByRole('button', { name: 'Выбрать условия публикации' }).click();

  const offer = page.getByRole('dialog', { name: 'Опубликовать виджет' });
  await expect(offer.getByText(/14 дней бесплатно/i)).toBeVisible();
  await expect(offer.getByRole('heading', { name: '500 ₽' })).toBeVisible();
  await expect(offer.getByRole('heading', { name: '2 000 ₽' })).toBeVisible();
  await expect(offer.getByRole('heading', { name: '5 000 ₽' })).toBeVisible();
  expect(await offer.evaluate((element) => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1);
  expect(await offer.locator('.publication-offer__plan').count()).toBe(3);
  const viewport = page.viewportSize();
  const modalBox = await publicationDialog.boundingBox();
  if (viewport && viewport.width <= 760) {
    expect(modalBox?.width ?? 0).toBeLessThanOrEqual(viewport.width);
  } else {
    expect(modalBox?.width ?? 0).toBeGreaterThan(900);
  }
});

test('Studio scales the 390 x 844 mobile reference viewport inside a narrow host @mobile', async ({ page, builderApi }) => {
  builderApi.seedRun('run-mobile-reference-viewport');
  await page.goto(`/studio?project=${builderApi.projectId}`);
  await page.getByRole('button', { name: 'Предпросмотр', exact: true }).click();
  await page.getByRole('button', { name: 'На телефоне' }).click();

  const device = page.locator('.studio-preview__device');
  await expect(device).toHaveAttribute('data-viewport', 'mobile');
  await expect(device).toHaveAttribute('data-viewport-width', '390');
  await expect(device).toHaveAttribute('data-viewport-height', '844');
  await expect.poll(async () => {
    const [canvasBox, frameBox] = await Promise.all([
      page.getByTestId('studio-preview-canvas').boundingBox(),
      page.getByTitle('Предпросмотр консультанта Kaigo').boundingBox(),
    ]);
    return Boolean(canvasBox && frameBox
      && frameBox.width <= 390
      && frameBox.width <= canvasBox.width
      && frameBox.x >= canvasBox.x
      && frameBox.y >= canvasBox.y
      && frameBox.x + frameBox.width <= canvasBox.x + canvasBox.width + 1
      && frameBox.y + frameBox.height <= canvasBox.y + canvasBox.height + 1);
  }).toBe(true);
  const internalViewport = await page.frameLocator('iframe[title="Предпросмотр консультанта Kaigo"]')
    .locator('body')
    .evaluate(() => ({ width: window.innerWidth, height: window.innerHeight }));
  expect(internalViewport).toEqual({ width: 390, height: 844 });
  await expectRuntimeOpensAndCloses(page);
  await expectNoHorizontalOverflow(page);
});

test('Studio mobile restores a SaaS project, switches preview and has no overflow @mobile', async ({ page, builderApi }) => {
  test.setTimeout(60_000);
  builderApi.seedRun('run-mobile');
  await page.goto(`/studio?project=${builderApi.projectId}`);

  await expect(page.getByRole('heading', { name: 'Чат с Kaigo' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Чат' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('region', { name: 'Предпросмотр виджета' })).toBeHidden();
  await page.getByRole('button', { name: 'Предпросмотр', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Предпросмотр', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('region', { name: 'Предпросмотр виджета' })).toBeVisible();
  await expect(page.getByTestId('studio-preview-canvas')).toHaveAttribute('data-viewport', 'desktop');
  await page.getByRole('button', { name: 'На телефоне' }).click();
  await expect(page.getByRole('button', { name: 'На телефоне' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByTestId('studio-preview-canvas')).toHaveAttribute('data-viewport', 'mobile');
  await expect(page.getByTitle('Предпросмотр консультанта Kaigo')).toHaveAttribute('sandbox', 'allow-scripts');
  await expect(page.getByRole('region', { name: 'Предпросмотр виджета' })).toContainText('Версия 2');
  await expectNoHorizontalOverflow(page);

  const viewportLock = await page.locator('.studio-app--workbench').evaluate((shell) => {
    const bounds = shell.getBoundingClientRect();
    return {
      top: bounds.top,
      bottom: bounds.bottom,
      documentOverflow: document.documentElement.scrollHeight - window.innerHeight,
    };
  });
  expect(viewportLock.top).toBe(0);
  expect(viewportLock.bottom).toBeLessThanOrEqual(845);
  expect(viewportLock.documentOverflow).toBeLessThanOrEqual(1);

  await page.getByRole('button', { name: 'Открыть версии' }).click();
  await expect(page.getByRole('dialog', { name: 'История версий' })).toBeVisible();
  await page.getByRole('button', { name: 'Закрыть панель' }).click();
  await expect(page.getByRole('dialog', { name: 'История версий' })).toHaveCount(0);

  await page.getByRole('button', { name: 'Открыть тариф и лимиты' }).click();
  const accountDrawer = page.getByRole('dialog', { name: 'Тариф и лимиты' });
  await expect(accountDrawer.getByText('Бесплатный режим')).toBeVisible();
  await expect(accountDrawer.getByText('Для продолжения нужен тариф')).toBeVisible();
  await accountDrawer.getByRole('button', { name: 'Закрыть панель' }).click();

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

  await expect(page.getByTitle('Предпросмотр консультанта Kaigo')).toBeVisible();
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
  await expect(page.getByTitle('Предпросмотр консультанта Kaigo')).toBeVisible();
  await page.getByRole('button', { name: 'Открыть тариф и лимиты' }).click();
  await expect(page.getByRole('dialog', { name: 'Тариф и лимиты' })).toBeVisible();

  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa'])
    .analyze();
  const blocking = results.violations.filter(({ impact }) =>
    impact === 'serious' || impact === 'critical',
  );
  expect(blocking).toEqual([]);
});
