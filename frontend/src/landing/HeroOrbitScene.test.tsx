import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { App } from '../App';

const setReducedMotion = (matches: boolean) => {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: query === '(prefers-reduced-motion: reduce)' ? matches : false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
};

describe('HeroOrbitScene', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setReducedMotion(false);
    window.history.replaceState({}, '', '/');
  });

  afterEach(() => {
    cleanup();
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it('runs the source, scanning and complete phases on the agreed timeline', () => {
    render(<App />);

    const scene = screen.getByTestId('hero-scene');
    expect(scene).toHaveAttribute('data-motion-phase', 'source');

    act(() => vi.advanceTimersByTime(1_800));
    expect(scene).toHaveAttribute('data-motion-phase', 'scanning');

    act(() => vi.advanceTimersByTime(5_600));
    expect(scene).toHaveAttribute('data-motion-phase', 'complete');
  });

  it('reveals three process cards and the completed widget', () => {
    render(<App />);

    act(() => vi.advanceTimersByTime(7_400));

    expect(screen.getAllByTestId('process-card')).toHaveLength(3);
    expect(screen.getByTestId('widget-preview')).toHaveAttribute('data-visible', 'true');
  });

  it('skips the timeline when reduced motion is requested', () => {
    setReducedMotion(true);

    render(<App />);

    expect(screen.getByTestId('hero-scene')).toHaveAttribute('data-motion-phase', 'complete');
    expect(screen.getByTestId('widget-preview')).toHaveAttribute('data-visible', 'true');
  });
});
