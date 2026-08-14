import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { PresentationGate } from './PresentationGate';

afterEach(() => {
  cleanup();
  window.history.replaceState({}, '', '/studio');
  vi.unstubAllGlobals();
});

describe('PresentationGate', () => {
  it('shows an allowlisted presentation only after operator access succeeds', async () => {
    window.history.replaceState({}, '', '/studio?presentation=founder-offer');
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ stages: [] }), { status: 200 }),
    );
    vi.stubGlobal('fetch', fetchMock);

    render(<PresentationGate><h1>Обычная студия</h1></PresentationGate>);

    expect(await screen.findByRole('heading', { name: /опубликовать виджет/i })).toBeVisible();
    expect(screen.getByText(/14 дней бесплатно/i)).toBeVisible();
    expect(screen.getByText(/осталось 20 мест/i)).toBeVisible();
    expect(screen.queryByRole('heading', { name: 'Обычная студия' })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/operator/funnel',
      expect.objectContaining({ credentials: 'same-origin' }),
    );
  });

  it('ignores the presentation query for every non-operator account', async () => {
    window.history.replaceState({}, '', '/studio?presentation=founder-offer');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 403 })));

    render(<PresentationGate><h1>Обычная студия</h1></PresentationGate>);

    expect(await screen.findByRole('heading', { name: 'Обычная студия' })).toBeVisible();
    expect(screen.queryByText(/14 дней бесплатно/i)).not.toBeInTheDocument();
  });

  it('ignores unknown presentation names without contacting the server', async () => {
    window.history.replaceState({}, '', '/studio?presentation=anything-else');
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    render(<PresentationGate><h1>Обычная студия</h1></PresentationGate>);

    expect(screen.getByRole('heading', { name: 'Обычная студия' })).toBeVisible();
    await waitFor(() => expect(fetchMock).not.toHaveBeenCalled());
  });

  it('opens the presentation index for the operator account', async () => {
    window.history.replaceState({}, '', '/studio?presentation=index');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ stages: [] }), { status: 200 }),
    ));

    render(<PresentationGate><h1>Обычная студия</h1></PresentationGate>);

    expect(await screen.findByRole('heading', { name: 'Экраны для ролика' })).toBeVisible();
    expect(screen.getByRole('link', { name: /первые 20 клиентов/i })).toHaveAttribute(
      'href',
      '/studio?presentation=founder-offer',
    );
  });
});
