import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';

import { FeedbackComposer } from './FeedbackComposer';

afterEach(cleanup);

describe('FeedbackComposer', () => {
  it('keeps the mail action unavailable until message and consent are both present', async () => {
    const user = userEvent.setup();
    render(<FeedbackComposer />);

    const action = screen.getByRole('link', { name: 'Открыть письмо' });
    expect(action).toHaveAttribute('aria-disabled', 'true');
    expect(action).not.toHaveAttribute('href');
    expect(screen.getByRole('link', { name: 'текстом согласия на обработку персональных данных' })).toHaveAttribute(
      'href',
      '/personal-data-consent/',
    );

    await user.type(screen.getByRole('textbox', { name: 'Сообщение' }), 'Хочу уточнить вопрос');
    expect(action).toHaveAttribute('aria-disabled', 'true');

    await user.click(screen.getByRole('checkbox', { name: /соглашаюсь на обработку/i }));

    expect(action).toHaveAttribute('aria-disabled', 'false');
    expect(action).toHaveAttribute('href', expect.stringContaining('mailto:support%40kaigo.space'));
    expect(decodeURIComponent(action.getAttribute('href') ?? '')).toContain('Хочу уточнить вопрос');
  });

  it('exposes exactly one selected topic and explains the mail application handoff', () => {
    render(<FeedbackComposer />);

    const topics = screen.getAllByRole('radio');
    expect(topics).toHaveLength(4);
    expect(topics.filter((topic) => (topic as HTMLInputElement).checked)).toHaveLength(1);
    expect(screen.getByText(/Откроется ваше почтовое приложение/i)).toBeVisible();
    expect(screen.getByText(/Ничего не отправляется автоматически/i)).toBeVisible();
  });
});
