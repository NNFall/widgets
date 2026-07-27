import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { App } from '../App';
import browserMockupSource from '../shared/BrowserMockup.tsx?raw';
import stylesSource from '../styles.css?raw';
import heroOrbitSceneSource from './HeroOrbitScene.tsx?raw';

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

  it('exposes the motion state and reveals cards sequentially on the cinematic timeline', () => {
    render(<App />);

    const scene = screen.getByTestId('hero-scene');
    const cards = screen.getAllByTestId('process-card');
    expect(scene).toHaveAttribute('data-motion-program', 'cinematic');
    expect(scene).toHaveAttribute('data-motion-phase', 'source');
    expect(scene).toHaveAttribute('data-motion-cycle', '0');
    expect(scene).toHaveAttribute('data-visible-cards', '0');
    expect(cards.map((card) => card.getAttribute('data-visible'))).toEqual(['false', 'false', 'false']);

    act(() => vi.advanceTimersByTime(1_200));
    expect(scene).toHaveAttribute('data-motion-phase', 'scanning');

    act(() => vi.advanceTimersByTime(1_200));
    expect(scene).toHaveAttribute('data-visible-cards', '1');
    expect(cards.map((card) => card.getAttribute('data-visible'))).toEqual(['true', 'false', 'false']);

    act(() => vi.advanceTimersByTime(1_600));
    expect(scene).toHaveAttribute('data-visible-cards', '2');

    act(() => vi.advanceTimersByTime(1_600));
    expect(scene).toHaveAttribute('data-visible-cards', '3');

    act(() => vi.advanceTimersByTime(1_900));
    expect(scene).toHaveAttribute('data-motion-phase', 'widget');
    expect(screen.getByTestId('widget-preview')).toHaveAttribute('data-visible', 'true');

    act(() => vi.advanceTimersByTime(900));
    expect(scene).toHaveAttribute('data-motion-phase', 'complete');
  });

  it('renders a visible scanner label while preserving the scene description', () => {
    render(<App />);

    act(() => vi.advanceTimersByTime(1_200));
    expect(screen.getByText('Сканирование…')).toBeVisible();
    expect(screen.getByTestId('hero-scanner')).toHaveAttribute('aria-hidden', 'true');
    expect(screen.getByTestId('hero-scanner-band')).toBeInTheDocument();
    expect(screen.getByTestId('hero-scanner-core')).toBeInTheDocument();
    expect(screen.getByTestId('hero-scanner-trail')).toBeInTheDocument();
    expect(screen.getAllByTestId('hero-scanner-particle')).toHaveLength(3);
    expect(screen.getByTestId('hero-scene')).toHaveAttribute(
      'aria-describedby',
      'hero-scene-description',
    );
  });

  it('marks each card with a distinct directional reveal and opts the hero into its own widget variant', () => {
    render(<App />);

    expect(
      screen.getAllByTestId('process-card').map((card) => card.getAttribute('data-reveal-direction')),
    ).toEqual(['upper-left', 'left', 'lower-left']);
    expect(screen.getByTestId('browser-mockup')).toHaveAttribute('data-variant', 'hero');
    expect(screen.getByTestId('widget-preview')).toHaveAttribute('data-variant', 'hero');
  });

  it('skips the timeline when reduced motion is requested', () => {
    setReducedMotion(true);

    render(<App />);

    const scene = screen.getByTestId('hero-scene');
    expect(scene).toHaveAttribute('data-motion-program', 'cinematic');
    expect(scene).toHaveAttribute('data-motion-phase', 'complete');
    expect(scene).toHaveAttribute('data-visible-cards', '3');
    expect(screen.getAllByTestId('process-card').every((card) => card.getAttribute('data-visible') === 'true')).toBe(true);
    expect(screen.getByTestId('widget-preview')).toHaveAttribute('data-visible', 'true');
    expect(vi.getTimerCount()).toBe(0);
  });

  it('moves to complete when reduced motion changes and removes its listener on cleanup', () => {
    const { unmount } = render(<App />);

    expect(screen.getByTestId('hero-scene')).toHaveAttribute('data-motion-phase', 'source');
    expect(reducedMotionController.listenerCount()).toBe(1);

    act(() => reducedMotionController.setMatches(true));
    expect(screen.getByTestId('hero-scene')).toHaveAttribute('data-motion-phase', 'complete');
    expect(screen.getByTestId('hero-scene')).toHaveAttribute('data-visible-cards', '3');
    expect(vi.getTimerCount()).toBe(0);

    act(() => vi.advanceTimersByTime(8_000));
    expect(screen.getByTestId('hero-scene')).toHaveAttribute('data-motion-phase', 'complete');

    unmount();
    expect(reducedMotionController.listenerCount()).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('uses immediate visual transitions when reduced motion is enabled dynamically', () => {
    expect(heroOrbitSceneSource).toMatch(
      /transition=\{\s*reducedMotion\s*\?\s*\{\s*duration:\s*0,\s*delay:\s*0\s*\}\s*:\s*motionComplete/,
    );
    expect(heroOrbitSceneSource).toMatch(
      /className="hero-browser-stage__scanner"[\s\S]*?transition=\{\{\s*duration:\s*reducedMotion\s*\?\s*0\s*:/,
    );
    expect(heroOrbitSceneSource).toMatch(
      /className="hero-browser-stage__widget-label"[\s\S]*?duration:\s*reducedMotion\s*\?\s*0[\s\S]*?delay:\s*reducedMotion\s*\?\s*0/,
    );
    expect(browserMockupSource).toMatch(
      /className="widget-preview"[\s\S]*?transition=\{\s*reducedMotion\s*\?\s*\{\s*duration:\s*0,\s*delay:\s*0\s*\}/,
    );
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

  it('matches the approved desktop geometry while resetting offsets on mobile', () => {
    const heroGridRules = [...stylesSource.matchAll(/\.hero-section__inner\s*\{([^}]*)\}/gs)].map((match) => match[1]);

    expect(heroGridRules.every((rule) => !rule.includes('transform:'))).toBe(true);
    expect(stylesSource).toMatch(/\.hero-scene\s*\{[^}]*left:\s*-4\.5%;/s);
    expect(stylesSource).toMatch(/\.hero-browser-stage\s*\{[^}]*top:\s*6%;/s);
    expect(stylesSource).toMatch(/\.browser-stack\s*\{[^}]*aspect-ratio:\s*0\.98;/s);
    expect(stylesSource).toContain('transform: translate3d(110px, 0, 0) scale(0.88) rotate(2.2deg);');
    expect(stylesSource).toMatch(/@media \(max-width:\s*980px\)[\s\S]*?\.hero-scene\s*\{[^}]*left:\s*0;/s);
    expect(stylesSource).toMatch(/@media \(max-width:\s*980px\)[\s\S]*?\.browser-stack\s*\{[^}]*aspect-ratio:\s*1\.27;/s);
  });

  it('progressively transforms the browser with card visibility and uses only current phase names', () => {
    expect(stylesSource).toMatch(
      /data-motion-phase='source'[\s\S]*?scale\(1\.1[2-6]\)\s+rotate\(-3deg\)/,
    );
    expect(stylesSource).toMatch(
      /data-motion-phase='scanning'\]\[data-visible-cards='1'\][\s\S]*?translate3d\(34px, 7px, 0\)\s+scale\(1\.06\)/,
    );
    expect(stylesSource).toMatch(
      /data-motion-phase='scanning'\]\[data-visible-cards='2'\][\s\S]*?translate3d\(67px, 4px, 0\)\s+scale\(0\.98\)/,
    );
    expect(stylesSource).toMatch(
      /data-motion-phase='scanning'\]\[data-visible-cards='3'\][\s\S]*?translate3d\(92px, 1px, 0\)\s+scale\(0\.92\)/,
    );
    expect(stylesSource).toContain('transform: translate3d(110px, 0, 0) scale(0.88) rotate(2.2deg);');
    expect(stylesSource).not.toContain("data-motion-phase='transforming'");
    expect(stylesSource).not.toContain("data-motion-phase='cards'");
  });

  it('gives only the hero widget the larger desktop and mobile payoff sizes', () => {
    expect(stylesSource).toMatch(
      /\.browser-stack--hero\s+\.widget-preview\s*\{[^}]*width:\s*47%;/s,
    );
    expect(stylesSource).toMatch(
      /@media \(max-width:\s*640px\)[\s\S]*?\.browser-stack--hero\s+\.widget-preview\s*\{[^}]*width:\s*58%;/s,
    );
    expect(browserMockupSource).toMatch(/variant\s*=\s*'default'/);
    expect(heroOrbitSceneSource).toMatch(/<BrowserMockup[\s\S]*?variant="hero"/);
  });

  it('hides the decorative browser mockup from assistive technology and describes the scene', () => {
    render(<App />);

    expect(screen.getByTestId('browser-mockup')).toHaveAttribute('aria-hidden', 'true');
    expect(screen.getByTestId('hero-scene')).toHaveAccessibleDescription(
      'Анимация показывает, как Kaigo анализирует исходный сайт и добавляет готовый AI-виджет.',
    );
  });
});
