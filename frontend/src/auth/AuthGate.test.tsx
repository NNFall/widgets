import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';

import { AuthGate } from './AuthGate';

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
  expect(screen.getByRole('link', { name: /Написать в поддержку/i })).toHaveAttribute(
    'href',
    'mailto:support@kaigo.space',
  );
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

it('offers direct support mail during a temporary authentication error', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === '/api/analytics/entry') return new Response(null, { status: 204 });
    if (url === '/api/auth/session') return new Response(null, { status: 503 });
    throw new Error(`unexpected request: ${url}`);
  }));

  render(<AuthGate><h1>Студия доступна</h1></AuthGate>);

  expect(await screen.findByRole('heading', { name: 'Студия сейчас не открылась' })).toBeVisible();
  const supportLink = screen.getByRole('link', { name: /Написать в поддержку/i });
  expect(supportLink).toHaveAttribute('href', 'mailto:support@kaigo.space');
  expect(supportLink).toHaveTextContent('support@kaigo.space');
});
