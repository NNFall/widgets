import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { ContactSection } from './ContactSection';

afterEach(cleanup);

describe('ContactSection', () => {
  it('offers a truthful contact band with provisional support and unconfirmed Telegram', () => {
    render(<ContactSection />);

    expect(screen.getByRole('region', { name: 'Связаться с командой Kaigo' })).toHaveAttribute('id', 'contact');
    expect(screen.getByRole('heading', { name: 'Есть вопрос или идея?' })).toBeVisible();
    expect(screen.getByRole('link', { name: 'support@kaigo.space' })).toHaveAttribute(
      'href',
      'mailto:support@kaigo.space',
    );
    expect(screen.getByRole('link', { name: 'support@kaigo.space' })).not.toHaveAttribute('href', expect.stringContaining('%40'));
    expect(screen.getByText('Добавим после подтверждения контакта')).toBeVisible();
    expect(screen.queryByRole('link', { name: /Telegram/i })).not.toBeInTheDocument();
  });
});
