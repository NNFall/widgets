import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { ContactSection } from './ContactSection';

afterEach(cleanup);

describe('ContactSection', () => {
  it('offers a truthful contact band with provisional support and unconfirmed Telegram', () => {
    render(<ContactSection />);

    expect(screen.getByRole('region', { name: 'Связаться с командой Kaigo' })).toHaveAttribute('id', 'contact');
    expect(screen.getByRole('heading', { name: 'Есть вопрос или идея?' })).toBeVisible();
    expect(screen.getByText('support@kaigo.space')).toBeVisible();
    expect(screen.getByText('Добавим после подтверждения контакта')).toBeVisible();
    expect(screen.queryByRole('link', { name: /Telegram/i })).not.toBeInTheDocument();
  });
});
