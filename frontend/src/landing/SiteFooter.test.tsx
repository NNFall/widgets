import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { SiteFooter } from './SiteFooter';

afterEach(() => {
  cleanup();
  window.history.replaceState({}, '', '/');
});

describe('SiteFooter', () => {
  it('links contact and all four legal documents while marking operator fields as preview placeholders', () => {
    render(<SiteFooter />);

    expect(screen.getByRole('contentinfo')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Связаться с командой' })).toHaveAttribute('href', '#contact');
    expect(screen.getByRole('link', { name: 'Политика конфиденциальности' })).toHaveAttribute('href', '/privacy/');
    expect(screen.getByRole('link', { name: 'Согласие на обработку данных' })).toHaveAttribute(
      'href',
      '/personal-data-consent/',
    );
    expect(screen.getByRole('link', { name: 'Условия использования' })).toHaveAttribute('href', '/terms/');
    expect(screen.getByRole('link', { name: 'Предварительная оферта' })).toHaveAttribute('href', '/offer/');
    expect(screen.getByText(/Оператор: уточняется до подтверждения/i)).toBeVisible();
    expect(screen.getByText(/ИНН: уточняется/i)).toBeVisible();
    expect(screen.getByText(/Адрес: уточняется/i)).toBeVisible();
  });

  it('keeps new footer destinations inside an isolated archive preview', () => {
    window.history.replaceState({}, '', '/frontend/v11/');
    render(<SiteFooter />);

    expect(screen.getByRole('link', { name: 'Связаться с командой' })).toHaveAttribute('href', '/frontend/v11/#contact');
    expect(screen.getByRole('link', { name: 'Политика конфиденциальности' })).toHaveAttribute('href', '/frontend/v11/privacy/');
    expect(screen.getByRole('link', { name: 'Студия' })).toHaveAttribute('href', '/frontend/v11/studio');
  });
});
