import { expect, test, type Page } from '@playwright/test';

async function authorizePresentation(page: Page) {
  await page.route('**/api/analytics/entry', (route) => route.fulfill({ status: 204 }));
  await page.route('**/api/auth/session', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      enabled: true,
      authenticated: true,
      user_id: 2,
      email: 'operator@example.com',
      csrf_token: 'presentation-csrf',
      providers: ['yandex'],
    }),
  }));
  await page.route('**/api/operator/funnel', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    headers: { 'Cache-Control': 'no-store' },
    body: JSON.stringify({ stages: [] }),
  }));
}

test('operator presentation links render real publication components @compact', async ({ page }, testInfo) => {
  await authorizePresentation(page);

  await page.goto('/studio?presentation=index');
  await expect(page.getByRole('heading', { name: 'Экраны для ролика' })).toBeVisible();
  await expect(page.getByRole('link', { name: /Первые 20 клиентов/i })).toBeVisible();

  await page.goto('/studio?presentation=founder-offer');
  const access = page.getByRole('region', { name: '14 дней полностью бесплатно' });
  await expect(access).toHaveCount(1);
  await expect(access).toBeVisible();
  await expect(access.getByRole('heading', {
    name: '14 дней полностью бесплатно',
  })).toBeVisible();
  await expect(access.getByRole('heading', { name: '500 ₽' })).toBeVisible();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.locator('.publication-offer__backdrop')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: /Опубликовать виджет/i })).toHaveCount(0);
  await expect(page.getByRole('button', {
    name: 'Выбрать условия публикации',
  })).toHaveCount(0);
  await testInfo.attach('founder-offer', {
    body: await page.screenshot({ fullPage: true }),
    contentType: 'image/png',
  });

  await page.goto('/studio?presentation=founder-active');
  await expect(page.getByLabel('На каких сайтах разрешить виджет')).toHaveCount(0);
  await expect(page.getByRole('button', {
    name: 'Опубликовать и получить код',
  })).toBeVisible();

  await page.goto('/studio?presentation=published');
  await expect(page.getByRole('heading', { name: 'Виджет опубликован' })).toBeVisible();
  await expect(page.getByLabel('На каких сайтах разрешить виджет')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Скопировать код установки' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Скопировать ссылку загрузчика' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Открыть инструкцию по установке' })).toHaveAttribute(
    'href',
    '/install',
  );
  await expect(page.getByText(/script src="https:\/\/kaigo\.space\/embed\/demo\.js/i)).toBeVisible();
  await testInfo.attach('published-widget', {
    body: await page.screenshot({ fullPage: true }),
    contentType: 'image/png',
  });
});
