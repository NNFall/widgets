import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';

import { AuthGate } from './AuthGate';

vi.mock('./VkOneTap', () => ({
  VkOneTap: ({ draftId, onSettled }: { draftId: string | null; onSettled: () => void }) => (
    <button
      type="button"
      data-testid="vk-one-tap"
      data-draft-id={draftId ?? ''}
      onClick={onSettled}
    >
      Войти с VK ID
    </button>
  ),
}));

afterEach(() => {
  cleanup();
  window.history.replaceState({}, '', '/');
  vi.unstubAllGlobals();
});

it('offers OAuth only for the opaque draft bound to this browser session', async () => {
  window.history.replaceState(
    {},
    '',
    '/studio?draft=draft-1',
  );
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (String(input) === '/api/analytics/entry') {
      return new Response(null, { status: 204 });
    }
    return new Response(JSON.stringify({
      enabled: true,
      authenticated: false,
      pending_draft_id: 'draft-1',
      providers: ['google', 'yandex'],
    }));
  });
  vi.stubGlobal('fetch', fetchMock);

  render(<AuthGate><h1>Закрытая студия</h1></AuthGate>);

  expect(await screen.findByRole('heading', { name: 'Сначала сохраните результат' })).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'Закрытая студия' })).not.toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Продолжить с Google' })).toHaveAttribute(
    'href',
    '/api/auth/google/start?draft_id=draft-1',
  );
  expect(screen.getByText(/платить нужно только за публикацию и подключение готового виджета/i)).toBeVisible();
  expect(screen.queryByText(/дорабат/i)).not.toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
    '/api/analytics/entry',
    '/api/auth/session',
  ]);
});

it('does not attach an unbound draft id to OAuth', async () => {
  window.history.replaceState({}, '', '/studio?draft=other-browser-draft');
  const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({
    enabled: true,
    authenticated: false,
    pending_draft_id: 'this-browser-draft',
    providers: ['google'],
  })));
  vi.stubGlobal('fetch', fetchMock);

  render(<AuthGate><h1>Закрытая студия</h1></AuthGate>);

  expect(await screen.findByRole('link', { name: 'Продолжить с Google' })).toHaveAttribute(
    'href',
    '/api/auth/google/start?draft_id=this-browser-draft',
  );
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it('adds VK One Tap without removing Google and Yandex', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    if (String(input) === '/api/analytics/entry') {
      return new Response(null, { status: 204 });
    }
    return new Response(JSON.stringify({
      enabled: true,
      authenticated: false,
      pending_draft_id: 'draft-vk',
      providers: ['google', 'vk', 'yandex'],
    }));
  }));

  render(<AuthGate><h1>Закрытая студия</h1></AuthGate>);

  const vkOneTap = await screen.findByTestId('vk-one-tap');
  expect(vkOneTap).toHaveAttribute('data-draft-id', 'draft-vk');
  expect(screen.queryByRole('link', { name: 'Продолжить с Google' })).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: 'Продолжить с Яндексом' })).not.toBeInTheDocument();

  await userEvent.click(vkOneTap);

  expect(screen.getByRole('link', { name: 'Продолжить с Google' })).toBeVisible();
  expect(screen.getByRole('link', { name: 'Продолжить с Яндексом' })).toBeVisible();
});

it('explains a cancelled OAuth callback in Russian and keeps retry actions', async () => {
  window.history.replaceState({}, '', '/studio?auth_error=access_denied');
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({
    enabled: true,
    authenticated: false,
    pending_draft_id: 'draft-retry',
    providers: ['google', 'yandex'],
  }))));

  render(<AuthGate><h1>Закрытая студия</h1></AuthGate>);

  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Вход отменён. Выберите способ входа и попробуйте ещё раз.',
  );
  expect(screen.getByRole('link', { name: 'Продолжить с Google' })).toHaveAttribute(
    'href',
    '/api/auth/google/start?draft_id=draft-retry',
  );
  expect(screen.getByRole('link', { name: 'Продолжить с Яндексом' })).toHaveAttribute(
    'href',
    '/api/auth/yandex/start?draft_id=draft-retry',
  );
});

it('explains when VK cannot be linked safely by email alone', async () => {
  window.history.replaceState({}, '', '/studio?auth_error=account_link_required');
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    if (String(input) === '/api/analytics/entry') {
      return new Response(null, { status: 204 });
    }
    return new Response(JSON.stringify({
      enabled: true,
      authenticated: false,
      pending_draft_id: null,
      providers: ['google', 'vk', 'yandex'],
    }));
  }));

  render(<AuthGate><h1>Закрытая студия</h1></AuthGate>);

  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Эта почта уже используется. Войдите прежним способом; VK ID можно будет подключить позже.',
  );
});

it('does not render an unknown OAuth error code from the URL', async () => {
  window.history.replaceState({}, '', '/studio?auth_error=client-secret-value');
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({
    enabled: true,
    authenticated: false,
    pending_draft_id: null,
    providers: ['google'],
  }))));

  render(<AuthGate><h1>Закрытая студия</h1></AuthGate>);

  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Не удалось завершить вход. Попробуйте ещё раз.',
  );
  expect(screen.queryByText('client-secret-value')).not.toBeInTheDocument();
});

it('claims a bound draft for an authenticated user before rendering Studio', async () => {
  window.history.replaceState({}, '', '/studio?draft=draft-brief-1');
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({
      enabled: true,
      authenticated: true,
      csrf_token: 'csrf-claim',
      pending_draft_id: 'draft-brief-1',
    })))
    .mockResolvedValueOnce(new Response(JSON.stringify({
      project: {
        id: 'project-from-draft',
        source_url: 'https://example.com/',
        brief: 'Brief survived',
      },
    }), { status: 201 }));
  vi.stubGlobal('fetch', fetchMock);

  render(<AuthGate><h1>Студия доступна</h1></AuthGate>);

  expect(await screen.findByRole('heading', { name: 'Студия доступна' })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/drafts/draft-brief-1/claim', expect.objectContaining({
    method: 'POST',
    credentials: 'include',
    headers: { 'X-CSRF-Token': 'csrf-claim' },
  }));
  expect(window.location.pathname).toBe('/studio');
  expect(Object.fromEntries(new URLSearchParams(window.location.search))).toEqual({
    project: 'project-from-draft',
    autostart: '1',
  });
});

it('does not invent OAuth buttons when no providers are configured', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
    new Response(JSON.stringify({
      enabled: true,
      authenticated: false,
      pending_draft_id: null,
      providers: [],
    })),
  ));

  render(<AuthGate><h1>Закрытая студия</h1></AuthGate>);

  expect(await screen.findByText('Вход временно недоступен')).toBeVisible();
  expect(screen.queryByRole('link', { name: /Продолжить с/ })).not.toBeInTheDocument();
});

it('renders Studio immediately for an authenticated browser session', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ enabled: true, authenticated: true, email: 'owner@example.com' })),
  ));

  render(<AuthGate><h1>Студия доступна</h1></AuthGate>);

  await waitFor(() => expect(screen.getByRole('heading', { name: 'Студия доступна' })).toBeInTheDocument());
});

it('recovers from a temporary session error without exposing raw failure text', async () => {
  let sessionAttempt = 0;
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === '/api/analytics/entry') return new Response(null, { status: 204 });
    if (url === '/api/auth/session') {
      sessionAttempt += 1;
      if (sessionAttempt === 1) return new Response(null, { status: 502 });
      return new Response(JSON.stringify({
        enabled: true,
        authenticated: true,
        email: 'owner@example.com',
      }));
    }
    throw new Error(`unexpected request: ${url}`);
  }));
  const user = userEvent.setup();

  render(<AuthGate><h1>Студия доступна</h1></AuthGate>);

  expect(await screen.findByRole('heading', { name: 'Студия сейчас не открылась' })).toBeVisible();
  expect(screen.queryByText('session:502')).not.toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Вернуться на главную' })).toHaveAttribute('href', '/');

  await user.click(screen.getByRole('button', { name: 'Повторить' }));
  expect(await screen.findByRole('heading', { name: 'Студия доступна' })).toBeVisible();
  expect(sessionAttempt).toBe(2);
});
