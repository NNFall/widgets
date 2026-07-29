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
    const mark = logo.querySelector('[data-kaigo-mark="K"]');
    expect.soft(mark).toBeInTheDocument();

    const strokeGeometry = Array.from(mark?.querySelectorAll('[data-kaigo-stroke]') ?? []).map((path) => ({
      stroke: path.getAttribute('data-kaigo-stroke'),
      d: path.getAttribute('d'),
    }));
    expect.soft(strokeGeometry).toEqual([
      { stroke: 'stem', d: 'M4 4h9v34H4z' },
      { stroke: 'upper-diagonal', d: 'M13 21 29 4h11L22 22z' },
      { stroke: 'lower-diagonal', d: 'M13 21h9l18 17H29z' },
    ]);

    const renderedPaths = Array.from(logo.querySelectorAll('path')).map((path) => path.getAttribute('d'));
    const legacyRPaths = [
      'M4 4h13v34H4z',
      'M21 4h4c8.3 0 13 4.2 13 10.1S33.3 24 25 24h-4V4Z',
      'M21 26h4c7 0 11 3 13 12H21V26Z',
    ];
    for (const legacyPath of legacyRPaths) {
      expect.soft(renderedPaths).not.toContain(legacyPath);
    }
    expect(screen.getAllByLabelText('Kaigo')).toHaveLength(1);
  });

  it('isolates gradient references between Kaigo logo instances', () => {
    render(
      <>
        <KaigoLogo />
        <KaigoLogo />
      </>,
    );

    const marks = screen.getAllByLabelText('Kaigo').map((logo) =>
      logo.querySelector('[data-kaigo-mark="K"]'),
    );
    const gradientIds = marks.map((mark) => mark?.querySelector('linearGradient')?.id);

    expect(gradientIds).toHaveLength(2);
    expect(new Set(gradientIds).size).toBe(2);
    for (const [index, mark] of marks.entries()) {
      const gradientId = gradientIds[index];
      expect(gradientId).toMatch(/^[A-Za-z0-9_-]+-kaigo-mark-gradient$/);
      expect(Array.from(mark?.querySelectorAll('path') ?? []).map((path) => path.getAttribute('fill')))
        .toEqual(Array(3).fill(`url(#${gradientId})`));
    }
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
      /\.site-header__menu-toggle\s*\{[^}]*width:\s*48px;[^}]*height:\s*48px/s,
    );
  });
});
