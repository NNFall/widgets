import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';

import stylesSource from '../styles.css?raw';
import { FeedbackComposer } from './FeedbackComposer';

afterEach(() => {
  cleanup();
  document.head.querySelectorAll('style[data-test-feedback-styles]').forEach((style) => style.remove());
});

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
    expect(action).toHaveAttribute('href', expect.stringContaining('mailto:support@kaigo.space?'));
    expect(action.getAttribute('href')).not.toContain('%40');
    expect(action.getAttribute('href')).toContain('%0D%0A');
    expect(decodeURIComponent(action.getAttribute('href') ?? '')).toContain('Хочу уточнить вопрос');
  });

  it('keeps the consent document link itself as a wrapping 44px touch target', () => {
    const style = document.createElement('style');
    style.dataset.testFeedbackStyles = 'true';
    style.textContent = stylesSource;
    document.head.append(style);
    render(<FeedbackComposer />);

    const consentLink = screen.getByRole('link', { name: 'текстом согласия на обработку персональных данных' });
    const computed = getComputedStyle(consentLink);
    expect(computed.minHeight).toBe('44px');
    expect(computed.display).toBe('inline-flex');
    expect(computed.whiteSpace).toBe('normal');
    expect(computed.overflowWrap).toBe('anywhere');
    expect(stylesSource).toMatch(
      /\.feedback-composer__consent a\s*\{[^}]*min-height:\s*44px;[^}]*display:\s*inline-flex;[^}]*overflow-wrap:\s*anywhere;/s,
    );
  });

  it('keeps the consent punctuation attached to the touch target', () => {
    render(<FeedbackComposer />);

    const consentLink = screen.getByRole('link', { name: 'текстом согласия на обработку персональных данных' });
    expect(consentLink).toHaveAttribute('aria-label', 'текстом согласия на обработку персональных данных');
    expect(consentLink.textContent).toMatch(/\.$/);
    expect(consentLink.children).toHaveLength(0);
    expect(consentLink.nextSibling).toBeNull();
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
