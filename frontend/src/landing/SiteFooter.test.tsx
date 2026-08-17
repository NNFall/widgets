import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import stylesSource from '../styles.css?raw';
import { SiteFooter } from './SiteFooter';

afterEach(() => {
  cleanup();
  document.head.querySelectorAll('style[data-test-footer-styles]').forEach((style) => style.remove());
  window.history.replaceState({}, '', '/');
});

describe('SiteFooter', () => {
  it('keeps only the compact contact/legal navigation and honest meta line', () => {
    render(<SiteFooter />);

    expect(screen.getByRole('contentinfo')).toBeInTheDocument();
    const navigation = screen.getByRole('navigation', { name: 'Ссылки в подвале' });
    expect(within(navigation).getByRole('link', { name: 'Связаться с командой' })).toHaveAttribute('href', '/#contact');
    expect(within(navigation).getByRole('link', { name: 'Политика конфиденциальности' })).toHaveAttribute('href', '/privacy/');
    expect(within(navigation).getByRole('link', { name: 'Согласие на обработку данных' })).toHaveAttribute(
      'href',
      '/personal-data-consent/',
    );
    expect(within(navigation).getByRole('link', { name: 'Условия использования' })).toHaveAttribute('href', '/terms/');
    expect(within(navigation).getByRole('link', { name: 'Предварительная оферта' })).toHaveAttribute('href', '/offer/');
    expect(screen.getByText('© 2026 Kaigo · Самозанятый, плательщик НПД · реквизиты уточняются')).toBeVisible();
    expect(screen.getByRole('link', { name: 'Kaigo — главная' })).toHaveAttribute('href', '/');
    expect(screen.queryByText(/Продукт|Как это работает|Кейсы|Помощь|Студия/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Оператор:|ФИО:|ИНН:|Адрес:/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/support@kaigo\.space|Telegram|mailto:/i)).not.toBeInTheDocument();
  });

  it('keeps new footer destinations inside an isolated archive preview', () => {
    window.history.replaceState({}, '', '/frontend/v11/');
    render(<SiteFooter />);

    expect(screen.getByRole('link', { name: 'Связаться с командой' })).toHaveAttribute('href', '/frontend/v11/#contact');
    expect(screen.getByRole('link', { name: 'Политика конфиденциальности' })).toHaveAttribute('href', '/frontend/v11/privacy/');
  });

  it('resolves footer hashes from a legal route back to the landing page', () => {
    window.history.replaceState({}, '', '/privacy/');
    render(<SiteFooter />);

    expect(screen.getByRole('link', { name: 'Связаться с командой' })).toHaveAttribute('href', '/#contact');
  });

  it('keeps footer links as a wrapping flex group with compact typography', () => {
    const style = document.createElement('style');
    style.dataset.testFooterStyles = 'true';
    style.textContent = stylesSource;
    document.head.append(style);
    render(<SiteFooter />);

    const navigation = screen.getByRole('navigation', { name: 'Ссылки в подвале' });
    expect(getComputedStyle(navigation).display).toBe('flex');
    expect(stylesSource).toMatch(/\.site-footer__links\s*\{[^}]*display:\s*flex/s);
    expect(stylesSource).toMatch(/\.site-footer__links a\s*\{[^}]*min-height:\s*44px;[^}]*font-size:\s*12px;/s);
    expect(stylesSource).not.toMatch(/\.site-footer__operator/);
  });

  it('keeps every footer link at a 44px touch target', () => {
    const style = document.createElement('style');
    style.dataset.testFooterStyles = 'true';
    style.textContent = stylesSource;
    document.head.append(style);
    render(<SiteFooter />);

    const links = within(screen.getByRole('navigation', { name: 'Ссылки в подвале' })).getAllByRole('link');
    links.forEach((link) => expect(getComputedStyle(link).minHeight).toBe('44px'));
  });
});
