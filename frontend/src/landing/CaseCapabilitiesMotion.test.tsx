import { act, cleanup, render, renderHook, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import stylesSource from '../styles.css?raw';
import capabilitiesSource from './CapabilitiesSection.tsx?raw';
import caseStudySource from './CaseStudySection.tsx?raw';
import { CapabilitiesSection, useCapabilityConversationCycle } from './CapabilitiesSection';
import { CaseStudySection } from './CaseStudySection';

vi.mock('motion/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('motion/react')>();

  return {
    ...actual,
    useInView: () => true,
    useReducedMotion: () => false,
  };
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('case study motion', () => {
  it('preserves the accessible toggle and marks the enhanced after payoff', async () => {
    const user = userEvent.setup();
    const { container } = render(<CaseStudySection />);
    const section = container.querySelector('.case-section');
    const before = screen.getByRole('button', { name: 'До' });
    const after = screen.getByRole('button', { name: 'После' });

    expect(section).toHaveAttribute('data-case-view', 'after');
    expect(container.querySelector('.case-panel--after')).toHaveClass('is-active');
    expect(container.querySelector('[data-case-widget="enhanced"]')).toBeInTheDocument();
    expect(container.querySelector('.case-after-halo')).toBeInTheDocument();

    await user.click(before);
    expect(section).toHaveAttribute('data-case-view', 'before');
    expect(before).toHaveAttribute('aria-pressed', 'true');

    await user.click(after);
    expect(section).toHaveAttribute('data-case-view', 'after');
    expect(after).toHaveAttribute('aria-pressed', 'true');
  });

  it('keeps both comparison panels in the mobile layout instead of display none', () => {
    const mobileStart = stylesSource.indexOf('@media (max-width: 767px) {\n  .case-comparison');
    const mobileEnd = stylesSource.indexOf('\n@media ', mobileStart + 1);
    const mobileRules = stylesSource.slice(mobileStart, mobileEnd);

    expect(mobileStart).toBeGreaterThan(-1);
    expect(mobileRules).not.toMatch(/\.case-panel\s*\{[^}]*display:\s*none/s);
    expect(mobileRules).toMatch(/\.case-panel-shell\s*\{[^}]*grid-area:\s*case/s);
    expect(mobileRules).toMatch(/\.case-panel\s*\{[^}]*opacity:\s*0/s);
    expect(mobileRules).toMatch(/\.case-panel\.is-mobile-active\s*\{[^}]*opacity:\s*1/s);
  });

  it('keeps Reveal inline motion on a shell outside the mobile crossfade layer', () => {
    expect(caseStudySource).toContain('className="case-panel-shell"');
    expect(caseStudySource).toMatch(
      /<Reveal className="case-panel-shell"[\s\S]*?<div[\s\S]*?className=\{`case-panel case-panel--before/,
    );
    expect(caseStudySource).toMatch(
      /<Reveal className="case-panel-shell"[\s\S]*?<div[\s\S]*?className=\{`case-panel case-panel--after/,
    );
  });

  it('hides only the inactive mobile panel from assistive technology and cleans the media listener', async () => {
    const user = userEvent.setup();
    let mediaListener: ((event: MediaQueryListEvent) => void) | undefined;
    const mediaQuery = {
      matches: true,
      media: '(max-width: 767px)',
      onchange: null,
      addEventListener: vi.fn((_event: string, listener: (event: MediaQueryListEvent) => void) => {
        mediaListener = listener;
      }),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    };
    vi.stubGlobal('matchMedia', vi.fn(() => mediaQuery));

    const { container, unmount } = render(<CaseStudySection />);
    const beforePanel = container.querySelector('.case-panel--before');
    const afterPanel = container.querySelector('.case-panel--after');

    expect(beforePanel).toHaveAttribute('aria-hidden', 'true');
    expect(beforePanel).toHaveAttribute('inert');
    expect(afterPanel).not.toHaveAttribute('aria-hidden');
    expect(afterPanel).not.toHaveAttribute('inert');

    await user.click(screen.getByRole('button', { name: 'До' }));
    expect(beforePanel).not.toHaveAttribute('aria-hidden');
    expect(beforePanel).not.toHaveAttribute('inert');
    expect(afterPanel).toHaveAttribute('aria-hidden', 'true');
    expect(afterPanel).toHaveAttribute('inert');

    mediaQuery.matches = false;
    act(() => mediaListener?.({ matches: false } as MediaQueryListEvent));
    expect(beforePanel).not.toHaveAttribute('aria-hidden');
    expect(afterPanel).not.toHaveAttribute('aria-hidden');

    unmount();
    expect(mediaQuery.removeEventListener).toHaveBeenCalledWith('change', mediaListener);
  });

  it('makes before quiet and after vivid with a larger widget and transform-only halo', () => {
    expect(stylesSource).toMatch(/\.case-panel--before \.browser-stack\s*\{[^}]*filter:\s*grayscale/s);
    expect(stylesSource).toMatch(/\.case-panel--after \.widget-preview\s*\{[^}]*width:\s*52%/s);
    expect(stylesSource).toMatch(/\[data-motion-active='true'\][^{}]*\.case-panel--after\.is-active[^{}]*\.case-after-halo\s*\{[^}]*animation:\s*case-widget-halo/s);

    const keyframes = stylesSource.match(/@keyframes case-widget-halo\s*\{([\s\S]*?)\n\}/)?.[1];
    expect(keyframes).toMatch(/(?:transform|opacity):/);
    expect(keyframes).not.toMatch(/(?:top|right|bottom|left|width|height|filter|box-shadow):/);
  });
});

describe('capability motion', () => {
  it('runs one phased conversation timer and pauses it with the activity boundary', () => {
    vi.useFakeTimers();
    const { result, rerender, unmount } = renderHook(
      ({ active, reducedMotion }) => useCapabilityConversationCycle(active, reducedMotion),
      { initialProps: { active: true, reducedMotion: false } },
    );

    expect(result.current).toBe('suggestion');
    expect(vi.getTimerCount()).toBe(1);

    act(() => vi.advanceTimersByTime(1800));
    expect(result.current).toBe('user');
    expect(vi.getTimerCount()).toBe(1);

    act(() => vi.advanceTimersByTime(1300));
    expect(result.current).toBe('typing');
    expect(vi.getTimerCount()).toBe(1);

    act(() => vi.advanceTimersByTime(1600));
    expect(result.current).toBe('answer');
    expect(vi.getTimerCount()).toBe(1);

    rerender({ active: false, reducedMotion: false });
    expect(vi.getTimerCount()).toBe(0);

    rerender({ active: true, reducedMotion: true });
    expect(result.current).toBe('answer');
    expect(vi.getTimerCount()).toBe(0);

    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('reveals the central chat first, then satellites in declared alternating order', () => {
    expect(capabilitiesSource).toContain('useMotionActivity<HTMLElement>()');
    expect(capabilitiesSource).toContain('useCapabilityConversationCycle(active, reducedMotion)');
    expect(capabilitiesSource).toContain('CAPABILITY_CHAT_REVEAL_DELAY_SECONDS');
    expect(capabilitiesSource).toMatch(/preset=\{revealOrder % 2 === 0 \? 'fromLeft' : 'fromRight'\}/);
    expect(capabilitiesSource).toContain('className="capability-item__icon"');
    expect(capabilitiesSource).toMatch(
      /<Reveal className="chat-showcase-reveal"[\s\S]*?<div className="chat-showcase">/,
    );
  });

  it('shows the restrained conversation states and gates icon-only float', () => {
    const { container } = render(<CapabilitiesSection />);
    const section = container.querySelector('.capabilities-section');

    expect(section).toHaveAttribute('data-motion-active', 'true');
    expect(container.querySelector('.chat-showcase__body')).toHaveAttribute('data-chat-phase', 'suggestion');
    expect(container.querySelectorAll('.capability-item__icon')).toHaveLength(6);
    expect(stylesSource).toMatch(
      /\[data-motion-active='true'\]\s+\.capability-item__icon\s*\{[^}]*animation:\s*capability-icon-float[^;]*infinite/s,
    );
    expect(stylesSource).not.toMatch(/\.capability-item\s*\{[^}]*animation:/s);
  });

  it('disables case and capability loops while preserving their final reduced-motion state', () => {
    const reducedStart = stylesSource.indexOf('@media (prefers-reduced-motion: reduce)');
    const reducedEnd = stylesSource.indexOf('@media (max-width: 1280px)', reducedStart);
    const reducedRules = stylesSource.slice(reducedStart, reducedEnd);

    expect(reducedRules).toContain('.case-after-halo');
    expect(reducedRules).toContain('.capability-item__icon');
    expect(reducedRules).toContain('animation: none !important;');
    expect(reducedRules).toMatch(/\.capability-chat__answer\s*\{[^}]*opacity:\s*1/s);
  });
});
