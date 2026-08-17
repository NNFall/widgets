import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { ContactSection } from './ContactSection';

afterEach(cleanup);

describe('ContactSection', () => {
  it('offers stored feedback without asking for contact details', () => {
    render(<ContactSection />);

    expect(screen.getByRole('region', { name: 'Связаться с командой Kaigo' })).toHaveAttribute('id', 'contact');
    expect(screen.getByRole('heading', { name: 'Есть вопрос или идея?' })).toBeVisible();
    expect(screen.getByText(
      'Напишите сообщение. Оно сохранится в Kaigo, контактные данные указывать не нужно.',
    )).toBeVisible();
    expect(screen.getByRole('button', { name: 'Отправить' })).toBeVisible();
    expect(screen.queryByText(/support@kaigo\.space|Telegram|почтовом приложении/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /почт/i })).not.toBeInTheDocument();
  });
});
