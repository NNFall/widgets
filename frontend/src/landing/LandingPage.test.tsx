import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';

import { LandingPage } from './LandingPage';

afterEach(cleanup);

describe('LandingPage sections', () => {
  it('renders the complete eight-screen marketing narrative', () => {
    render(<LandingPage />);

    expect(screen.getByRole('heading', { name: 'Как это работает' })).toBeVisible();
    expect(screen.getByRole('heading', { name: 'Что видит Kaigo' })).toBeVisible();
    expect(screen.getByRole('heading', { name: /Не просто чат/ })).toBeVisible();
    expect(screen.getByRole('heading', { name: 'Первый вариант — только начало' })).toBeVisible();
    expect(document.querySelectorAll('section[data-landing-section]')).toHaveLength(8);
    expect(screen.getAllByRole('textbox', { name: /ссылка на.*сайт/i })).toHaveLength(2);
  });

  it('exposes an accessible before and after case switch', async () => {
    const user = userEvent.setup();
    render(<LandingPage />);

    const before = screen.getByRole('button', { name: 'До' });
    const after = screen.getByRole('button', { name: 'После' });

    expect(after).toHaveAttribute('aria-pressed', 'true');
    await user.click(before);
    expect(before).toHaveAttribute('aria-pressed', 'true');
    expect(after).toHaveAttribute('aria-pressed', 'false');
  });

  it('opens and closes FAQ answers with native buttons', async () => {
    const user = userEvent.setup();
    render(<LandingPage />);

    const timeQuestion = screen.getByRole('button', { name: /Сколько времени/ });
    expect(timeQuestion).toHaveAttribute('aria-expanded', 'false');

    await user.click(timeQuestion);
    expect(timeQuestion).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText(/первую версию/i)).toBeVisible();

    await user.click(timeQuestion);
    expect(timeQuestion).toHaveAttribute('aria-expanded', 'false');
  });
});
