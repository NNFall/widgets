import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { App } from './App';

afterEach(() => {
  cleanup();
  window.history.replaceState({}, '', '/');
});

describe('App', () => {
  it('renders the landing page contract', () => {
    window.history.replaceState({}, '', '/');

    render(<App />);

    expect(
      screen.getByRole('heading', {
        name: 'Через 10 минут вы сможете сказать: наш бизнес использует AI',
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Перейти в студию' })).toHaveAttribute(
      'href',
      '/studio',
    );
    expect(document.querySelectorAll('section[data-landing-section]')).toHaveLength(8);
  });

  it('renders the studio placeholder at /studio', () => {
    window.history.replaceState({}, '', '/studio');

    render(<App />);

    expect(screen.getByRole('heading', { name: 'Студия Kaigo' })).toBeInTheDocument();
    expect(document.querySelectorAll('section[data-landing-section]')).toHaveLength(0);
  });
});
