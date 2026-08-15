import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { LegalPage } from './LegalPage';
import { LEGAL_DOCUMENTS } from './legalDocuments';

afterEach(() => {
  cleanup();
  window.history.replaceState({}, '', '/');
});

describe('legal information architecture', () => {
  it.each([
    ['privacy', 'Политика конфиденциальности'],
    ['personal-data-consent', 'Согласие на обработку персональных данных'],
    ['terms', 'Условия использования'],
    ['offer', 'Предварительная публичная оферта'],
  ] as const)('renders the %s document with shared preview structure', (documentId, title) => {
    render(<LegalPage document={LEGAL_DOCUMENTS[documentId]} />);

    expect(screen.getByRole('heading', { name: title, level: 1 })).toBeVisible();
    expect(screen.getByText('Тестовая редакция')).toBeVisible();
    expect(screen.getByText(/Редакция для предпросмотра/i)).toBeVisible();
    expect(screen.getByRole('link', { name: 'Вернуться на главную' })).toHaveAttribute('href', '/');
    expect(screen.getByRole('navigation', { name: 'Содержание документа' })).toBeInTheDocument();
    expect(screen.getByRole('contentinfo')).toBeInTheDocument();
    expect(screen.getAllByText(/support@kaigo\.space/).length).toBeGreaterThan(0);
  });

  it('keeps uncertain operator and backend facts visibly marked instead of inventing requisites', () => {
    render(<LegalPage document={LEGAL_DOCUMENTS.privacy} />);

    const main = screen.getByRole('main');
    expect(within(main).getAllByText(/уточняется до подтверждения/i).length).toBeGreaterThan(0);
    expect(within(main).getByText(/Срок хранения и точный маршрут данных уточняются/i)).toBeVisible();
    expect(within(main).queryByText(/уведомил Роскомнадзор/i)).not.toBeInTheDocument();
  });

  it('keeps the legal page navigation inside an isolated archive preview', () => {
    window.history.replaceState({}, '', '/frontend/v11/privacy/');
    render(<LegalPage document={LEGAL_DOCUMENTS.privacy} />);

    expect(screen.getByRole('link', { name: 'Вернуться на главную' })).toHaveAttribute('href', '/frontend/v11/');
    expect(screen.getByRole('link', { name: '1. Какие данные могут поступить' })).toHaveAttribute(
      'href',
      '#privacy-data',
    );
  });
});
