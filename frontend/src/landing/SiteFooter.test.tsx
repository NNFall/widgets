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
  it('links contact and all four legal documents while marking operator fields as preview placeholders', () => {
    render(<SiteFooter />);

    expect(screen.getByRole('contentinfo')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Связаться с командой' })).toHaveAttribute('href', '/#contact');
    expect(screen.getByRole('link', { name: 'Политика конфиденциальности' })).toHaveAttribute('href', '/privacy/');
    expect(screen.getByRole('link', { name: 'Согласие на обработку данных' })).toHaveAttribute(
      'href',
      '/personal-data-consent/',
    );
    expect(screen.getByRole('link', { name: 'Условия использования' })).toHaveAttribute('href', '/terms/');
    expect(screen.getByRole('link', { name: 'Предварительная оферта' })).toHaveAttribute('href', '/offer/');
    expect(screen.getByText(/Оператор: уточняется до подтверждения/i)).toBeVisible();
    expect(screen.getByText(/Статус, указанный владельцем: самозанятый, плательщик НПД/i)).toBeVisible();
    expect(screen.queryByText(/подтверждено владельцем/i)).not.toBeInTheDocument();
    expect(screen.getByText(/ФИО: уточняется/i)).toBeVisible();
    expect(screen.getByText(/ИНН: уточняется/i)).toBeVisible();
    expect(screen.getByText(/Адрес: уточняется/i)).toBeVisible();
    expect(screen.getByRole('link', { name: 'support@kaigo.space' })).toHaveAttribute(
      'href',
      'mailto:support@kaigo.space',
    );
  });

  it('keeps new footer destinations inside an isolated archive preview', () => {
    window.history.replaceState({}, '', '/frontend/v11/');
    render(<SiteFooter />);

    expect(screen.getByRole('link', { name: 'Связаться с командой' })).toHaveAttribute('href', '/frontend/v11/#contact');
    expect(screen.getByRole('link', { name: 'Политика конфиденциальности' })).toHaveAttribute('href', '/frontend/v11/privacy/');
    expect(screen.getByRole('link', { name: 'Студия' })).toHaveAttribute('href', '/frontend/v11/studio');
  });

  it('resolves footer hashes from a legal route back to the landing page', () => {
    window.history.replaceState({}, '', '/privacy/');
    render(<SiteFooter />);

    expect(screen.getByRole('link', { name: 'Продукт' })).toHaveAttribute('href', '/#product');
    expect(screen.getByRole('link', { name: 'Как это работает' })).toHaveAttribute('href', '/#product-tour');
    expect(screen.getByRole('link', { name: 'Кейсы' })).toHaveAttribute('href', '/#case-study');
    expect(screen.getByRole('link', { name: 'Помощь' })).toHaveAttribute('href', '/#faq');
    expect(screen.getByRole('link', { name: 'Связаться с командой' })).toHaveAttribute('href', '/#contact');
  });

  it('keeps footer navigation as a grid instead of inheriting the legacy flex rule', () => {
    const style = document.createElement('style');
    style.dataset.testFooterStyles = 'true';
    style.textContent = stylesSource;
    document.head.append(style);
    render(<SiteFooter />);

    const navigation = screen.getByRole('navigation', { name: 'Навигация в подвале' });
    expect(getComputedStyle(navigation).display).toBe('grid');
    expect(stylesSource).not.toMatch(/\.site-footer nav\s*\{[^}]*display:\s*flex/s);
    expect(stylesSource).toMatch(/\.landing-page \.site-footer__nav[\s\S]*display:\s*grid/s);
  });

  it('keeps the standalone operator mail link at a 44px touch target', () => {
    const style = document.createElement('style');
    style.dataset.testFooterStyles = 'true';
    style.textContent = stylesSource;
    document.head.append(style);
    render(<SiteFooter />);

    const mailLink = within(document.querySelector('.site-footer__operator') as HTMLElement).getByRole('link', {
      name: 'support@kaigo.space',
    });
    expect(getComputedStyle(mailLink).minHeight).toBe('44px');
  });
});
