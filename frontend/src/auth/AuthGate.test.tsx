import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

import { AuthGate } from './AuthGate';

afterEach(() => {
  cleanup();
  window.history.replaceState({}, '', '/');
  vi.unstubAllGlobals();
});

it('creates an anonymous draft and offers OAuth before paid generation starts', async () => {
  window.history.replaceState({}, '', '/studio?url=https%3A%2F%2Fexample.com');
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ enabled: true, authenticated: false })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ id: 'draft-1' }), { status: 201 }));
  vi.stubGlobal('fetch', fetchMock);

  render(<AuthGate><h1>Закрытая студия</h1></AuthGate>);

  expect(await screen.findByRole('heading', { name: 'Сначала сохраните результат' })).toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'Закрытая студия' })).not.toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Продолжить с Google' })).toHaveAttribute(
    'href',
    '/api/auth/google/start?draft_id=draft-1',
  );
  expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/drafts', expect.objectContaining({ method: 'POST' }));
});

it('renders Studio immediately for an authenticated browser session', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ enabled: true, authenticated: true, email: 'owner@example.com' })),
  ));

  render(<AuthGate><h1>Студия доступна</h1></AuthGate>);

  await waitFor(() => expect(screen.getByRole('heading', { name: 'Студия доступна' })).toBeInTheDocument());
});
