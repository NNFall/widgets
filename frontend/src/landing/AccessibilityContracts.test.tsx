import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import indexHtmlSource from '../../index.html?raw';
import stylesSource from '../styles.css?raw';
import { KaigoLogo } from '../shared/KaigoLogo';

afterEach(cleanup);

describe('landing accessibility contracts', () => {
  it('exposes the Kaigo lockup as one accessible image with the shared Living Fold mark', () => {
    render(<KaigoLogo />);

    const logo = screen.getByRole('img', { name: 'Kaigo' });
    expect(within(logo).getByText('Kaigo')).toBeInTheDocument();
    const mark = logo.querySelector('[data-kaigo-mark="living-fold"]');
    expect(mark).toBeInTheDocument();
    expect(mark?.tagName).toBe('IMG');
    expect(mark).toHaveAttribute('src', '/assets/brand/kaigo-living-fold-mark.png');
    expect(mark).toHaveAttribute('alt', '');
    expect(mark).toHaveAttribute('aria-hidden', 'true');
    expect(logo.querySelector('svg')).not.toBeInTheDocument();
    expect(logo.querySelector('path')).not.toBeInTheDocument();
    expect(logo.querySelector('linearGradient')).not.toBeInTheDocument();
    expect(screen.getAllByLabelText('Kaigo')).toHaveLength(1);
  });

  it('keeps the Living Fold mark independent from the legacy tone prop', () => {
    render(
      <>
        <KaigoLogo />
        <KaigoLogo tone="coral" />
      </>,
    );

    const marks = screen.getAllByLabelText('Kaigo').map((logo) =>
      logo.querySelector('[data-kaigo-mark="living-fold"]'),
    );

    expect(marks).toHaveLength(2);
    expect(marks.map((mark) => mark?.getAttribute('src'))).toEqual([
      '/assets/brand/kaigo-living-fold-mark.png',
      '/assets/brand/kaigo-living-fold-mark.png',
    ]);
    for (const mark of marks) {
      expect(mark?.querySelector('svg, path, linearGradient, stop')).not.toBeInTheDocument();
    }
  });

  it('declares the PNG favicon in the HTML shell', () => {
    const htmlDocument = new DOMParser().parseFromString(indexHtmlSource, 'text/html');
    const favicon = htmlDocument.querySelector('link[rel="icon"]');

    expect(favicon).toBeTruthy();
    expect(favicon?.getAttribute('href')).toBe('/favicon.png');
    expect(favicon?.getAttribute('type')).toBe('image/png');
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

  it('uses accessible text colors across the warm landing palette', () => {
    expect(stylesSource).toMatch(
      /\.landing-page\s*\{[^}]*--coral-text:\s*#a83212;/s,
    );
    expect(stylesSource).toMatch(
      /\.widget-preview-card__input\s*\{[^}]*color:\s*#59636b;/s,
    );
    expect(stylesSource).toMatch(
      /\.widget-preview-card__source\s*\{[^}]*color:\s*#59636b;/s,
    );
    expect(stylesSource).toMatch(
      /\.case-panel--after \.case-panel__label > span\s*\{[^}]*background:\s*#dfe2e4;/s,
    );
    expect(stylesSource).toMatch(
      /\.capability-chat__user\s*\{[^}]*background:\s*#dfe2e4;/s,
    );
  });
});
