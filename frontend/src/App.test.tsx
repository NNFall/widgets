import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { App } from './App';

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    if (String(input) === '/api/projects') {
      return new Response(JSON.stringify({ projects: [] }));
    }
    return new Response(JSON.stringify({ enabled: false, authenticated: false }));
  }));
});

afterEach(() => {
  cleanup();
  window.history.replaceState({}, '', '/');
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('App', () => {
  it('renders the selected Kaigo hero and full navigation', () => {
    render(<App />);

    expect(screen.getByRole('heading', { name: /Покажите сайт.*получите первую версию.*AI-консультанта/i })).toBeInTheDocument();
    expect(
      Array.from(document.querySelectorAll('.hero-title-line'), (line) => line.textContent),
    ).toEqual([
      'Покажите сайт.',
      'Получите первую версию',
      'AI-консультанта',
    ]);
    expect(screen.getByText(/Kaigo бесплатно создаст первую версию AI-виджета/i)).toBeVisible();
    const navigation = within(screen.getByRole('navigation', { name: 'Основная навигация' }));
    expect(navigation.getByRole('link', { name: 'Продукт' })).toHaveAttribute('href', '#product');
    expect(navigation.getByRole('link', { name: 'Как это работает' })).toHaveAttribute(
      'href',
      '#how-it-works',
    );
    expect(navigation.getByRole('link', { name: 'Кейсы' })).toHaveAttribute('href', '#case-study');
    expect(navigation.getByRole('link', { name: 'Вопросы' })).toHaveAttribute('href', '#faq');
    const desktopHeader = screen.getByRole('banner').querySelector('.site-header__inner');
    expect(within(desktopHeader as HTMLElement).getByRole('link', { name: 'Перейти в студию' })).toHaveAttribute(
      'href',
      '/studio',
    );
    expect(document.querySelectorAll('section[data-landing-section]')).toHaveLength(9);
  });

  it('preserves only allowlisted campaign parameters on every Studio navigation link', () => {
    window.history.replaceState(
      {},
      '',
      '/?utm_source=telegram&utm_campaign=launch&email=private%40example.com&utm_unknown=discard',
    );

    render(<App />);

    const studioLinks = Array.from(document.querySelectorAll<HTMLAnchorElement>('a[href^="/studio"]'));
    expect(studioLinks.length).toBeGreaterThan(1);
    for (const link of studioLinks) {
      expect(link.getAttribute('href')).toBe('/studio?utm_source=telegram&utm_campaign=launch');
    }
  });

  it('stores URL and brief server-side and navigates with only an opaque draft id', async () => {
    window.history.replaceState(
      {},
      '',
      '/?utm_source=telegram&utm_medium=social&utm_campaign=launch&utm_term=ai&utm_content=hero&email=private%40example.com&utm_unknown=discard',
    );
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === '/api/drafts') {
        return new Response(JSON.stringify({ id: 'draft-opaque-1' }), { status: 201 });
      }
      if (String(input) === '/api/auth/session') {
        return new Response(JSON.stringify({ enabled: false, authenticated: false }));
      }
      throw new Error(`unexpected request: ${String(input)}`);
    });
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText('Ссылка на действующий сайт'), 'https://FAẞ.DE:443/услуги');
    await user.type(
      screen.getAllByRole('textbox', { name: 'Пожелание к AI-виджету' })[0],
      'Отвечай кратко',
    );
    await user.click(screen.getAllByRole('button', { name: 'Получить бесплатную версию' })[0]);

    await waitFor(() => expect(window.location.pathname).toBe('/studio'));
    const destination = new URLSearchParams(window.location.search);
    expect(Object.fromEntries(destination)).toEqual({ draft: 'draft-opaque-1' });
    expect(window.location.href).not.toContain('fass.de');
    expect(window.location.href).not.toContain(encodeURIComponent('Отвечай кратко'));
    expect(fetchMock).toHaveBeenCalledWith('/api/drafts', expect.objectContaining({
      method: 'POST',
      credentials: 'include',
      body: JSON.stringify({
        url: 'https://fass.de/%D1%83%D1%81%D0%BB%D1%83%D0%B3%D0%B8',
        brief: 'Отвечай кратко',
        campaign: {
          utm_source: 'telegram',
          utm_medium: 'social',
          utm_campaign: 'launch',
          utm_term: 'ai',
          utm_content: 'hero',
        },
      }),
    }));
  });

  it.each([
    'example.com',
    'http://example.com',
    'https://user:secret@example.com',
    'https://example.com:8443',
    'https://example.com/?token=secret',
    'https://example.com/#section',
    'https://127.0.0.1/private',
    'https://localhost/private',
    'https://example.com\\private',
  ])('shows a Russian inline error for an incompatible website URL: %s', async (value) => {
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText('Ссылка на действующий сайт'), value);
    await user.click(screen.getAllByRole('button', { name: 'Получить бесплатную версию' })[0]);

    expect(screen.getByRole('alert')).toHaveTextContent('Введите публичный HTTPS-адрес без параметров и авторизации');
    expect(window.location.pathname).toBe('/');
  });

  it('keeps the mobile navigation untabbable while closed and exposes it when opened', async () => {
    const user = userEvent.setup();
    render(<App />);

    const toggle = screen.getByRole('button', { name: 'Открыть меню' });
    const mobileNavigation = document.getElementById('mobile-navigation') as HTMLElement;
    const mobileProductLink = mobileNavigation.querySelector('a[href="#product"]') as HTMLElement;
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(mobileNavigation).toHaveAttribute('hidden');
    expect(mobileProductLink).not.toBeVisible();

    await user.click(toggle);

    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(mobileNavigation).not.toHaveAttribute('hidden');
    expect(screen.getByRole('navigation', { name: 'Мобильная навигация' })).toBeVisible();
    expect(mobileProductLink).toBeVisible();
  });

  it('closes mobile navigation and restores focus after deferred hash navigation', async () => {
    let frameCallback: FrameRequestCallback | undefined;
    const requestFrame = vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      frameCallback = callback;
      return 41;
    });
    const cancelFrame = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => undefined);
    const user = userEvent.setup();
    const { unmount } = render(<App />);

    const toggle = screen.getByRole('button', { name: 'Открыть меню' });
    await user.click(toggle);

    const mobileNavigation = screen.getByRole('navigation', { name: 'Мобильная навигация' });
    const productLink = within(mobileNavigation).getByRole('link', { name: 'Продукт' });
    productLink.focus();
    await user.click(productLink);

    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(document.getElementById('mobile-navigation')).toHaveAttribute('hidden');
    expect(requestFrame).toHaveBeenCalledOnce();
    expect(toggle).not.toHaveFocus();

    act(() => frameCallback?.(performance.now()));
    expect(toggle).toHaveFocus();

    await user.click(toggle);
    await user.click(within(screen.getByRole('navigation', { name: 'Мобильная навигация' })).getByRole('link', { name: 'Продукт' }));
    unmount();

    expect(cancelFrame).toHaveBeenCalledWith(41);
  });

  it.each(['/studio', '/studio/'])('renders the functional Studio at %s', async (pathname) => {
    window.history.replaceState({}, '', pathname);

    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Мои виджеты' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Создайте новый виджет' })).toBeInTheDocument();
    expect(document.querySelectorAll('section[data-landing-section]')).toHaveLength(0);
  });

  it('renders the landing page for paths that only start with /studio', () => {
    window.history.replaceState({}, '', '/studio-preview');

    render(<App />);

    expect(screen.getByRole('heading', { name: /Покажите сайт.*получите первую версию.*AI-консультанта/i })).toBeInTheDocument();
    expect(document.querySelectorAll('section[data-landing-section]')).toHaveLength(9);
  });
});
