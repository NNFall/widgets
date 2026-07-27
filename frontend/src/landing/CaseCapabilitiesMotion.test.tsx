import { act, cleanup, render, renderHook, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import stylesSource from '../styles.css?raw';
import capabilitiesSource from './CapabilitiesSection.tsx?raw';
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
    const mobileStart = stylesSource.lastIndexOf('@media (max-width: 767px)');
    const mobileRules = stylesSource.slice(mobileStart);

    expect(mobileRules).not.toMatch(/\.case-panel\s*\{[^}]*display:\s*none/s);
    expect(mobileRules).toMatch(/\.case-panel\s*\{[^}]*grid-area:\s*case/s);
    expect(mobileRules).toMatch(/\.case-panel\.is-mobile-active\s*\{[^}]*opacity:\s*1/s);
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
