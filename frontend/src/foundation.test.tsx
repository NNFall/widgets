import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import mainSource from './main.tsx?raw';
import './styles.css';

afterEach(cleanup);

describe('frontend foundation', () => {
  it('applies the landing viewport only to marked sections', () => {
    render(
      <>
        <section data-testid="generic-section">Generic content</section>
        <section data-testid="landing-section" data-landing-section>
          Landing content
        </section>
      </>,
    );

    expect(getComputedStyle(screen.getByTestId('generic-section')).minHeight).not.toBe('18rem');
    expect(getComputedStyle(screen.getByTestId('landing-section')).minHeight).toBe('18rem');
  });

  it('loads the required Manrope weights without font synthesis', () => {
    expect(mainSource).toContain("@fontsource/manrope/400.css");
    expect(mainSource).toContain("@fontsource/manrope/600.css");
    expect(mainSource).toContain("@fontsource/manrope/700.css");
    expect(getComputedStyle(document.documentElement).fontSynthesis).toBe('none');
  });
});
