import { act, cleanup, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const activityState = vi.hoisted(() => ({
  active: true,
  reducedMotion: false,
}));

vi.mock('../shared/MotionActivity', () => ({
  useMotionActivity: () => ({
    active: activityState.active,
    reducedMotion: activityState.reducedMotion,
    ref: { current: null },
  }),
}));

vi.mock('../shared/Reveal', () => ({
  Reveal: ({ children, className }: { children: ReactNode; className?: string }) => (
    <div className={className}>{children}</div>
  ),
}));

vi.mock('../shared/BrowserMockup', () => ({
  BrowserMockup: ({ motionComplete, reducedMotion }: { motionComplete: boolean; reducedMotion: boolean }) => (
    <div
      data-testid="analysis-browser-mockup"
      data-motion-complete={motionComplete ? 'true' : 'false'}
      data-reduced-motion={reducedMotion ? 'true' : 'false'}
    />
  ),
}));

import { AnalysisSection } from './AnalysisSection';

const activeObservation = (kind: 'lens' | 'note') => document.querySelector(
  `[data-analysis-kind="${kind}"][data-analysis-active="true"]`,
);

describe('AnalysisSection observation choreography', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    activityState.active = true;
    activityState.reducedMotion = false;
  });

  afterEach(() => {
    cleanup();
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it('advances focus, lens, and note through one sequential activity-gated phase', () => {
    const { rerender } = render(<AnalysisSection />);

    expect(screen.getByTestId('analysis-focus-ring')).toHaveAttribute('data-analysis-focus', 'content');
    expect(activeObservation('lens')).toHaveAttribute('data-analysis-observation', 'content');
    expect(activeObservation('note')).toHaveAttribute('data-analysis-observation', 'content');
    expect(vi.getTimerCount()).toBe(1);

    act(() => vi.advanceTimersByTime(1_800));
    expect(screen.getByTestId('analysis-focus-ring')).toHaveAttribute('data-analysis-focus', 'visual');
    expect(activeObservation('lens')).toHaveAttribute('data-analysis-observation', 'visual');
    expect(activeObservation('note')).toHaveAttribute('data-analysis-observation', 'visual');
    expect(vi.getTimerCount()).toBe(1);

    act(() => vi.advanceTimersByTime(1_800));
    expect(screen.getByTestId('analysis-focus-ring')).toHaveAttribute('data-analysis-focus', 'structure');

    act(() => vi.advanceTimersByTime(1_800));
    expect(screen.getByTestId('analysis-focus-ring')).toHaveAttribute('data-analysis-focus', 'questions');
    expect(activeObservation('lens')).toHaveAttribute('data-analysis-observation', 'questions');
    expect(activeObservation('note')).toHaveAttribute('data-analysis-observation', 'questions');

    activityState.active = false;
    rerender(<AnalysisSection />);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('separates activity pause from the real reduced-motion preference', () => {
    activityState.active = false;
    const { rerender } = render(<AnalysisSection />);
    const browser = screen.getByTestId('analysis-browser-mockup');

    expect(browser).toHaveAttribute('data-motion-complete', 'false');
    expect(browser).toHaveAttribute('data-reduced-motion', 'false');

    activityState.reducedMotion = true;
    rerender(<AnalysisSection />);
    expect(browser).toHaveAttribute('data-reduced-motion', 'true');
  });
});
