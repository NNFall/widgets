import { act, cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { App } from './App';

afterEach(() => {
  cleanup();
  window.history.replaceState({}, '', '/');
  vi.restoreAllMocks();
});

describe('App', () => {
  it('renders the selected Kaigo hero and full navigation', () => {
    render(<App />);

    expect(screen.getByRole('heading', { name: /Через 10 минут.*использует AI/i })).toBeInTheDocument();
    expect(
      Array.from(document.querySelectorAll('.hero-title-line'), (line) => line.textContent),
    ).toEqual([
      'Через 10 минут',
      'вы сможете сказать:',
      'наш бизнес',
      'использует AI',
    ]);
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
    expect(document.querySelectorAll('section[data-landing-section]')).toHaveLength(8);
  });

  it('routes a valid website URL to Studio with an encoded query', async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText('Ссылка на действующий сайт'), 'https://пример.рф/услуги');
    await user.click(screen.getByRole('button', { name: 'Создать AI-виджет' }));

    expect(window.location.pathname).toBe('/studio');
    expect(new URLSearchParams(window.location.search).get('url')).toBe('https://пример.рф/услуги');
  });

  it('shows a Russian inline error for an invalid website URL', async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText('Ссылка на действующий сайт'), 'example.com');
    await user.click(screen.getByRole('button', { name: 'Создать AI-виджет' }));

    expect(screen.getByRole('alert')).toHaveTextContent('Введите полный адрес сайта с http:// или https://');
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

  it.each(['/studio', '/studio/'])('renders the functional Studio at %s', (pathname) => {
    window.history.replaceState({}, '', pathname);

    render(<App />);

    expect(screen.getByRole('heading', { name: 'Студия Kaigo' })).toBeInTheDocument();
    expect(document.querySelectorAll('section[data-landing-section]')).toHaveLength(0);
  });

  it('renders the landing page for paths that only start with /studio', () => {
    window.history.replaceState({}, '', '/studio-preview');

    render(<App />);

    expect(screen.getByRole('heading', { name: /Через 10 минут.*использует AI/i })).toBeInTheDocument();
    expect(document.querySelectorAll('section[data-landing-section]')).toHaveLength(8);
  });
});
