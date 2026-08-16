import AxeBuilder from '@axe-core/playwright';

import { expect, test } from './fixtures/builder';
import type { Locator, Page, TestInfo } from '@playwright/test';

const PHONE_VIEWPORTS = [
  { width: 320, height: 568 },
  { width: 360, height: 800 },
  { width: 390, height: 844 },
  { width: 430, height: 932 },
] as const;

function viewportsForProject(testInfo: TestInfo) {
  return testInfo.project.name === 'desktop-1920'
    ? [{ width: 1_920, height: 1_080 }]
    : PHONE_VIEWPORTS;
}

async function expectNoHorizontalOverflow(page: import('@playwright/test').Page) {
  const overflow = await page.evaluate(() =>
    Math.max(
      document.documentElement.scrollWidth,
      document.body.scrollWidth,
    ) - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
}

async function expectDrawerFits(dialog: Locator, label: string) {
  // The drawer enters with a 220ms translate animation; measure its settled geometry.
  await new Promise((resolve) => setTimeout(resolve, 300));
  const geometry = await dialog.evaluate((element) => {
    const bounds = element.getBoundingClientRect();
    return {
      left: bounds.left,
      right: bounds.right,
      width: bounds.width,
      viewportWidth: window.innerWidth,
      innerOverflow: element.scrollWidth - element.clientWidth,
    };
  });
  expect(geometry.left, `${label} left edge`).toBeGreaterThanOrEqual(-1);
  expect(geometry.right, `${label} right edge`).toBeLessThanOrEqual(geometry.viewportWidth + 1);
  expect(geometry.width, `${label} width`).toBeLessThanOrEqual(geometry.viewportWidth + 1);
  expect(geometry.innerOverflow, `${label} inner horizontal overflow`).toBeLessThanOrEqual(1);
}

async function expectContactTargetsAreTouchSafe(dialog: Locator, label: string) {
  const undersized = await dialog.locator(
    'button:visible, a[href]:visible, textarea:visible, .feedback-topic:visible, .feedback-composer__consent:visible',
  ).evaluateAll((elements) => elements.flatMap((element) => {
    const bounds = element.getBoundingClientRect();
    if (bounds.width >= 44 && bounds.height >= 44) return [];
    return [{
      name: element.getAttribute('aria-label') || element.textContent?.trim().slice(0, 80) || element.tagName,
      width: Math.round(bounds.width * 10) / 10,
      height: Math.round(bounds.height * 10) / 10,
    }];
  }));
  expect(undersized, `Studio contact targets at ${label}`).toEqual([]);
}

async function expectFocusInside(page: Page, dialog: Locator, label: string) {
  expect(
    await dialog.evaluate((element) => element.contains(document.activeElement)),
    `${label} focus must remain inside the dialog`,
  ).toBe(true);
}

async function expectDrawerAccessibility(page: Page, label: string) {
  const results = await new AxeBuilder({ page })
    .include('.studio-drawer')
    .withTags(['wcag2a', 'wcag2aa'])
    .analyze();
  const blocking = results.violations.filter(({ impact }) =>
    impact === 'serious' || impact === 'critical',
  );
  expect(blocking, `${label} drawer accessibility`).toEqual([]);
}

async function preventMailtoNavigation(page: Page) {
  await page.addInitScript(() => {
    document.addEventListener('click', (event) => {
      const target = event.target instanceof Element
        ? event.target.closest<HTMLAnchorElement>('a[href^="mailto:"]')
        : null;
      if (target) event.preventDefault();
    }, true);
  });
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
  await expect(page.getByRole('list', { name: 'Этапы создания виджета' }).getByRole('listitem')).toHaveCount(7);
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
  await expect(page.getByTitle('Предпросмотр AI-сотрудника Kaigo')).toHaveAttribute(
    'src',
    /\/api\/runs\/run-resume\/preview\/document\?revision=4&channel=/,
  );
  expect(await page.evaluate(() => localStorage.getItem('kaigo.builder.activeRun.v1'))).toBeNull();

  const projectReadsBeforeReload = builderApi.requests.filter(({ method, pathname }) =>
    method === 'GET' && pathname === `/api/projects/${builderApi.projectId}`,
  ).length;
  await page.reload();
  await expect(page.locator('details.studio-conversation__context')).not.toHaveAttribute('open');
  await expect(page.getByTitle('Предпросмотр AI-сотрудника Kaigo')).toBeVisible();
  await expect(page.getByRole('region', { name: 'Предпросмотр виджета' })).toContainText('Версия 2');
  await expect.poll(() => builderApi.requests.filter(({ method, pathname }) =>
    method === 'GET' && pathname === `/api/projects/${builderApi.projectId}`,
  ).length).toBeGreaterThan(projectReadsBeforeReload);
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

test('active subscription publishes the current verified artifact with a stable embed snippet @desktop', async ({ page, builderApi }) => {
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
  const publicationDrawer = page.getByRole('dialog', { name: 'Публикация виджета' });
  const drawerOverflow = await publicationDrawer.evaluate((element) =>
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
  await expect(page.getByTitle('Предпросмотр AI-сотрудника Kaigo')).toHaveAttribute('sandbox', 'allow-scripts');
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

test('Studio home and composer expose support without a mutating feedback API call @mobile @desktop', async ({ page, builderApi }, testInfo) => {
  test.setTimeout(180_000);
  await preventMailtoNavigation(page);
  const viewports = viewportsForProject(testInfo);
  let drawerA11yChecked = false;

  for (const viewport of viewports) {
    const viewportLabel = `${viewport.width}x${viewport.height}`;
    await page.setViewportSize(viewport);

    for (const pathname of ['/studio', `/studio?project=${builderApi.projectId}`]) {
      await page.goto(pathname);
      await expect(page.getByRole('button', { name: 'Помощь', exact: true })).toBeVisible();
      const helpButton = page.getByRole('button', { name: 'Помощь', exact: true });
      await helpButton.focus();
      const requestCountBeforeContact = builderApi.requests.length;
      await helpButton.click();

      const contactDialog = page.getByRole('dialog', { name: 'Помощь и обратная связь' });
      await expect(contactDialog).toBeVisible();
      await expectDrawerFits(contactDialog, `${pathname} contact drawer at ${viewportLabel}`);
      await expectContactTargetsAreTouchSafe(contactDialog, `${pathname} at ${viewportLabel}`);
      await expectFocusInside(page, contactDialog, `${pathname} at ${viewportLabel}`);
      if (!drawerA11yChecked && (viewport.width === 320 || testInfo.project.name === 'desktop-1920')) {
        await expectDrawerAccessibility(page, `${pathname} at ${viewportLabel}`);
        drawerA11yChecked = true;
      }

      const composer = contactDialog.locator('form.feedback-composer');
      await composer.getByLabel('Сообщение').fill(`Проверка Studio ${viewportLabel}`);
      await composer.locator('input[type="checkbox"]').check();
      const mailLink = composer.getByRole('link', { name: 'Открыть письмо' });
      await expect(mailLink).toHaveAttribute(
        'href',
        /^mailto:support@kaigo\.space\?subject=/,
      );
      const requestCountBeforeClick = builderApi.requests.length;
      await mailLink.click();
      await expect(mailLink).toBeVisible();
      await page.waitForTimeout(100);
      const actionMutations = builderApi.requests.slice(requestCountBeforeClick).filter(({ method, pathname: requestPath }) =>
        ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method.toUpperCase())
        && /feedback|contact|support|message/i.test(requestPath),
      );
      expect(actionMutations, `${pathname} must not mutate feedback at ${viewportLabel}`).toEqual([]);
      expect(
        builderApi.requests.slice(requestCountBeforeContact).filter(({ method, pathname: requestPath }) =>
          ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method.toUpperCase())
          && /feedback|contact|support|message/i.test(requestPath),
        ),
        `${pathname} must not call a feedback API at ${viewportLabel}`,
      ).toEqual([]);

      await page.keyboard.press('Escape');
      await expect(contactDialog).toHaveCount(0);
      await expect(helpButton).toBeFocused();
      await expectNoHorizontalOverflow(page);
    }
  }
});

test('Studio account opens support, traps keyboard focus and closes on Escape @mobile @desktop', async ({ page, builderApi }, testInfo) => {
  test.setTimeout(180_000);
  await preventMailtoNavigation(page);
  builderApi.seedRun('run-contact-mobile');
  const viewports = viewportsForProject(testInfo);
  let drawerA11yChecked = false;

  for (const viewport of viewports) {
    const viewportLabel = `${viewport.width}x${viewport.height}`;
    await page.setViewportSize(viewport);
    await page.goto(`/studio?project=${builderApi.projectId}`);
    await expect(page.getByRole('heading', { name: 'Чат с Kaigo' })).toBeVisible();

    const accountButton = page.getByRole('button', { name: 'Открыть тариф и лимиты' });
    await accountButton.focus();
    await accountButton.click();
    const accountDialog = page.getByRole('dialog', { name: 'Тариф и лимиты' });
    await expect(accountDialog).toBeVisible();
    await expectDrawerFits(accountDialog, `account drawer at ${viewportLabel}`);

    await accountDialog.getByRole('button', { name: 'Помощь и обратная связь' }).click();
    const contactDialog = page.getByRole('dialog', { name: 'Помощь и обратная связь' });
    await expect(contactDialog).toBeVisible();
    await expect(accountDialog).toHaveCount(0);
    await expectDrawerFits(contactDialog, `account contact drawer at ${viewportLabel}`);
    await expectContactTargetsAreTouchSafe(contactDialog, `account contact at ${viewportLabel}`);
    await expectFocusInside(page, contactDialog, `account contact at ${viewportLabel}`);
    if (!drawerA11yChecked && (viewport.width === 320 || testInfo.project.name === 'desktop-1920')) {
      await expectDrawerAccessibility(page, `account contact at ${viewportLabel}`);
      drawerA11yChecked = true;
    }

    const composer = contactDialog.locator('form.feedback-composer');
    await composer.getByLabel('Сообщение').fill(`Проверка account Studio ${viewportLabel}`);
    await composer.locator('input[type="checkbox"]').check();
    const mailLink = composer.getByRole('link', { name: 'Открыть письмо' });
    await expect(mailLink).toHaveAttribute('href', /^mailto:support@kaigo\.space\?subject=/);
    const requestCountBeforeClick = builderApi.requests.length;
    await mailLink.click();
    await expect(mailLink).toBeVisible();
    await page.waitForTimeout(100);
    const actionMutations = builderApi.requests.slice(requestCountBeforeClick).filter(({ method, pathname: requestPath }) =>
      ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method.toUpperCase())
      && /feedback|contact|support|message/i.test(requestPath),
    );
    expect(actionMutations, `account contact must not mutate feedback at ${viewportLabel}`).toEqual([]);

    const focusables = contactDialog.locator(
      'button:not([disabled]), a[href], input:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    );
    const focusableCount = await focusables.count();
    expect(focusableCount, `contact dialog has focusable controls at ${viewportLabel}`).toBeGreaterThan(1);
    const firstFocusable = focusables.first();
    const lastFocusable = focusables.last();
    await firstFocusable.focus();
    await page.keyboard.press('Shift+Tab');
    await expect(lastFocusable).toBeFocused();
    await lastFocusable.focus();
    await page.keyboard.press('Tab');
    await expect(firstFocusable).toBeFocused();

    await page.keyboard.press('Escape');
    await expect(contactDialog).toHaveCount(0);
    await expectNoHorizontalOverflow(page);
  }
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
