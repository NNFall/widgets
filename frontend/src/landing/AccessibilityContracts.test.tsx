import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import stylesSource from '../styles.css?raw';
import { KaigoLogo } from '../shared/KaigoLogo';

afterEach(cleanup);

describe('landing accessibility contracts', () => {
  it('exposes the Kaigo lockup as one valid image semantic', () => {
    render(<KaigoLogo />);

    const logo = screen.getByRole('img', { name: 'Kaigo' });
    expect(within(logo).getByText('Kaigo')).toBeInTheDocument();
    expect(screen.getAllByLabelText('Kaigo')).toHaveLength(1);
  });

  it('uses dark ink on sage and coral interactive-state surfaces', () => {
    expect(stylesSource).toMatch(
      /\.case-panel--after \.case-panel__label > span\s*\{[^}]*color:\s*var\(--ink\)/s,
    );
    expect(stylesSource).toMatch(
      /\.case-toggle button\[aria-pressed='true'\]\s*\{[^}]*color:\s*var\(--ink\)/s,
    );
    expect(stylesSource).toMatch(
      /\.studio-demo__publish-label\s*\{[^}]*color:\s*var\(--ink\)/s,
    );
    expect(stylesSource).toMatch(
      /\.capability-chat__user\s*\{[^}]*color:\s*var\(--ink\)/s,
    );
  });

  it('keeps mobile landing controls at least 44 pixels tall and wide', () => {
    expect(stylesSource).toMatch(
      /\.case-toggle button\s*\{[^}]*min-height:\s*44px/s,
    );
    expect(stylesSource).toMatch(
      /\.studio-demo__sidebar > button\s*\{[^}]*width:\s*50px;[^}]*height:\s*50px/s,
    );
  });
});
