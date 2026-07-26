import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { App } from '../App';
import stylesSource from '../styles.css?raw';

type ReducedMotionListener = (event: { matches: boolean }) => void;

const setReducedMotion = (initialMatches: boolean) => {
  let matches = initialMatches;
  const listeners = new Set<ReducedMotionListener>();
  const mediaQuery = {
    get matches() {
      return matches;
    },
    media: '(prefers-reduced-motion: reduce)',
    onchange: null,
    addEventListener: vi.fn((_event: string, listener: ReducedMotionListener) => listeners.add(listener)),
    removeEventListener: vi.fn((_event: string, listener: ReducedMotionListener) => listeners.delete(listener)),
    addListener: vi.fn((listener: ReducedMotionListener) => listeners.add(listener)),
    removeListener: vi.fn((listener: ReducedMotionListener) => listeners.delete(listener)),
    dispatchEvent: vi.fn(),
  };

  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn().mockImplementation(() => mediaQuery),
  });

  return {
    setMatches(nextMatches: boolean) {
      matches = nextMatches;
      listeners.forEach((listener) => listener({ matches }));
    },
    listenerCount() {
      return listeners.size;
    },
  };
};

let reducedMotionController: ReturnType<typeof setReducedMotion>;

describe('HeroOrbitScene', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    reducedMotionController = setReducedMotion(false);
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

  it('moves to complete when reduced motion changes and removes its listener on cleanup', () => {
    const { unmount } = render(<App />);

    expect(screen.getByTestId('hero-scene')).toHaveAttribute('data-motion-phase', 'source');
    expect(reducedMotionController.listenerCount()).toBe(1);

    act(() => reducedMotionController.setMatches(true));
    expect(screen.getByTestId('hero-scene')).toHaveAttribute('data-motion-phase', 'complete');

    act(() => vi.advanceTimersByTime(8_000));
    expect(screen.getByTestId('hero-scene')).toHaveAttribute('data-motion-phase', 'complete');

    unmount();
    expect(reducedMotionController.listenerCount()).toBe(0);
  });

  it('locks the desktop headline width and pulses through transform and opacity only', () => {
    expect(stylesSource).toMatch(/\.hero-section\s*\{[^}]*overflow(?:-x)?:\s*clip;/s);
    expect(stylesSource).toMatch(/\.hero-copy h1\s*\{[^}]*max-width:\s*10\.8ch;/s);
    expect(stylesSource).toContain('.widget-preview::before');

    const pulseKeyframes = stylesSource.match(/@keyframes widget-pulse\s*\{([\s\S]*?)\n\}/)?.[1];
    expect(pulseKeyframes).toContain('transform:');
    expect(pulseKeyframes).toContain('opacity:');
    expect(pulseKeyframes).not.toContain('box-shadow:');
    expect(stylesSource).not.toContain('@keyframes widget-ring');
  });

  it('hides the decorative browser mockup from assistive technology and describes the scene', () => {
    render(<App />);

    expect(screen.getByTestId('browser-mockup')).toHaveAttribute('aria-hidden', 'true');
    expect(screen.getByTestId('hero-scene')).toHaveAccessibleDescription(
      'Анимация показывает, как Kaigo анализирует исходный сайт и добавляет готовый AI-виджет.',
    );
  });
});
