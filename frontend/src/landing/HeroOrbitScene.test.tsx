import { act, cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import browserMockupSource from '../shared/BrowserMockup.tsx?raw';
import stylesSource from '../styles.css?raw';
import { HeroOrbitScene } from './HeroOrbitScene';
import heroOrbitSceneSource from './HeroOrbitScene.tsx?raw';

vi.mock('motion/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('motion/react')>();
  return { ...actual, useInView: () => true, useReducedMotion: () => false };
});

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

  const fixedMediaQuery = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  });

  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => (
      query === mediaQuery.media ? mediaQuery : fixedMediaQuery(query)
    )),
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
    render(<HeroOrbitScene />);

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
    render(<HeroOrbitScene />);

    act(() => vi.advanceTimersByTime(1_200));
    const scanner = screen.getByTestId('hero-scanner');
    expect(scanner).toHaveClass('hero-browser-stage__scanner');
    expect(scanner).toHaveAttribute('data-active', 'true');
    expect(within(scanner).getByText('Сканирование…')).toBeVisible();
    expect(scanner).toHaveAttribute('aria-hidden', 'true');
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
    render(<HeroOrbitScene />);

    expect(
      screen.getAllByTestId('process-card').map((card) => card.getAttribute('data-reveal-direction')),
    ).toEqual(['upper-left', 'left', 'lower-left']);
    expect(screen.getByTestId('browser-mockup')).toHaveAttribute('data-variant', 'hero');
    expect(screen.getByTestId('widget-preview')).toHaveAttribute('data-variant', 'hero');
  });

  it('uses actual upper-left, left, and lower-left card vectors on desktop and mobile', () => {
    const readVectors = (source: string) => [1, 2, 3].map((card) => {
      const rule = [...source.matchAll(new RegExp(`\\.process-card--${card}\\s*\\{([^}]*)\\}`, 'g'))]
        .map((match) => match[1])
        .find((body) => body.includes('--card-hidden-x'));
      const x = Number(rule?.match(/--card-hidden-x:\s*(-?\d+)px;/)?.[1]);
      const y = Number(rule?.match(/--card-hidden-y:\s*(-?\d+)px;/)?.[1]);
      return { x, y };
    });
    const desktopStyles = stylesSource.slice(0, stylesSource.indexOf('@media (max-width: 1280px)'));
    const mobileStyles = stylesSource.slice(
      stylesSource.indexOf('@media (max-width: 767px)'),
      stylesSource.indexOf('@media (max-width: 640px)'),
    );

    expect(readVectors(desktopStyles)).toEqual([
      { x: -112, y: -22 },
      { x: -84, y: 0 },
      { x: -120, y: 22 },
    ]);
    expect(readVectors(mobileStyles)).toEqual([
      { x: -58, y: -12 },
      { x: -44, y: 0 },
      { x: -62, y: 14 },
    ]);
  });

  it('skips the timeline when reduced motion is requested', () => {
    setReducedMotion(true);

    render(<HeroOrbitScene />);

    const scene = screen.getByTestId('hero-scene');
    expect(scene).toHaveAttribute('data-motion-program', 'cinematic');
    expect(scene).toHaveAttribute('data-motion-phase', 'complete');
    expect(scene).toHaveAttribute('data-visible-cards', '3');
    expect(screen.getAllByTestId('process-card').every((card) => card.getAttribute('data-visible') === 'true')).toBe(true);
    expect(screen.getByTestId('widget-preview')).toHaveAttribute('data-visible', 'true');
  });

  it('moves to complete when reduced motion changes and removes its listener on cleanup', () => {
    const { unmount } = render(<HeroOrbitScene />);

    expect(screen.getByTestId('hero-scene')).toHaveAttribute('data-motion-phase', 'source');
    expect(reducedMotionController.listenerCount()).toBe(1);

    act(() => reducedMotionController.setMatches(true));
    expect(screen.getByTestId('hero-scene')).toHaveAttribute('data-motion-phase', 'complete');
    expect(screen.getByTestId('hero-scene')).toHaveAttribute('data-visible-cards', '3');

    act(() => vi.advanceTimersByTime(8_000));
    expect(screen.getByTestId('hero-scene')).toHaveAttribute('data-motion-phase', 'complete');

    unmount();
    expect(reducedMotionController.listenerCount()).toBe(0);
  });

  it('uses immediate visual transitions when reduced motion is enabled dynamically', () => {
    expect(heroOrbitSceneSource).toMatch(
      /transition=\{\s*reducedMotion\s*\?\s*\{\s*duration:\s*0,\s*delay:\s*0\s*\}\s*:\s*motionComplete/,
    );
    expect(heroOrbitSceneSource).toMatch(
      /className="hero-browser-stage__scanner"[\s\S]*?data-active=\{phase === 'scanning'/,
    );
    expect(heroOrbitSceneSource).toMatch(
      /className="hero-browser-stage__widget-label"[\s\S]*?duration:\s*reducedMotion\s*\?\s*0[\s\S]*?delay:\s*reducedMotion\s*\?\s*0\s*:\s*widgetVisible\s*\?\s*0\.25\s*:\s*0/,
    );
    expect(browserMockupSource).toMatch(
      /className="widget-preview"[\s\S]*?transition=\{\s*reducedMotion\s*\?\s*\{\s*duration:\s*0,\s*delay:\s*0\s*\}/,
    );
  });

  it('gates the hero timeline and every infinite decorative loop behind motion activity', () => {
    expect(heroOrbitSceneSource).toContain('useMotionActivity<HTMLDivElement>()');
    expect(heroOrbitSceneSource).toContain('useHeroMotionCycle(activityActive)');
    expect(heroOrbitSceneSource).toContain('const motionActive = activityActive && !reducedMotion');
    expect(heroOrbitSceneSource).toContain("data-motion-active={motionActive ? 'true' : 'false'}");
    expect(browserMockupSource).toContain('motionActive: boolean');
    expect(browserMockupSource).toContain('motionComplete && motionActive && !reducedMotion');
    expect(stylesSource).toMatch(
      /\.hero-scene\[data-motion-active='true'\][^{}]*\.widget-preview__shimmer\s*\{[^}]*animation:\s*hero-rest-shimmer/s,
    );
    expect(stylesSource).toMatch(
      /\.hero-scene\[data-motion-phase='scanning'\][^{}]*\.hero-browser-stage__scanner-particle\s*\{[^}]*animation:\s*hero-scanner-particle[^}]*animation-play-state:\s*paused/s,
    );
    expect(stylesSource).toMatch(
      /\.hero-scene\[data-motion-active='true'\]\[data-motion-phase='scanning'\][^{}]*\.hero-browser-stage__scanner-particle\s*\{[^}]*animation-play-state:\s*running/s,
    );
    expect(stylesSource).toMatch(
      /\.hero-scene\[data-motion-phase='scanning'\][^{}]*\.hero-browser-stage__scanner\[data-active='true'\]\s*\{[^}]*animation:\s*hero-scanner-sweep[^}]*animation-play-state:\s*paused/s,
    );
    expect(stylesSource).toMatch(
      /\.hero-scene\[data-motion-active='true'\]\[data-motion-phase='scanning'\][^{}]*\.hero-browser-stage__scanner\[data-active='true'\]\s*\{[^}]*animation-play-state:\s*running/s,
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

  it('returns resetting browser geometry to each breakpoint source position within 600ms', () => {
    const neutralRules = [...stylesSource.matchAll(
      /\.hero-scene\[data-motion-phase='source'\]\s+\.hero-browser-stage,\s*\.hero-scene\[data-motion-phase='resetting'\]\s+\.hero-browser-stage,\s*\.hero-scene\[data-motion-phase='scanning'\]\[data-visible-cards='0'\]\s+\.hero-browser-stage\s*\{([^}]*)\}/g,
    )].map((match) => match[1]);

    expect(neutralRules).toHaveLength(4);
    expect(neutralRules[0]).toContain('translate3d(0, 10px, 0) scale(1.14) rotate(-3deg)');
    expect(neutralRules[1]).toContain('translate3d(120px, 10px, 0) scale(1.14) rotate(-3deg)');
    expect(neutralRules[2]).toContain('translate3d(-4%, 6%, 0) scale(1.1) rotate(-2.2deg)');
    expect(neutralRules[3]).toContain('translate3d(-10%, 12%, 0) scale(1.06) rotate(-1.8deg)');
    expect(stylesSource).toMatch(
      /data-motion-phase='resetting'\]\s+\.hero-browser-stage\s*\{[^}]*transition-duration:\s*600ms;/s,
    );
    expect(stylesSource).not.toMatch(
      /data-motion-phase='complete'\]\s+\.hero-browser-stage,\s*\.hero-scene\[data-motion-phase='resetting'\]/,
    );
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

  it('limits widget will-change to the arrival phase', () => {
    expect(stylesSource).toMatch(
      /data-motion-phase='widget'\]\s+\.browser-stack--hero\s+\.widget-preview\s*\{[^}]*will-change:\s*transform, opacity;/s,
    );
    expect(stylesSource).not.toMatch(
      /data-motion-phase='complete'\]\s+\.browser-stack--hero\s+\.widget-preview[\s\S]{0,120}will-change:/,
    );
  });

  it('hides the decorative browser mockup from assistive technology and describes the scene', () => {
    render(<HeroOrbitScene />);

    expect(screen.getByTestId('browser-mockup')).toHaveAttribute('aria-hidden', 'true');
    expect(screen.getByTestId('hero-scene')).toHaveAccessibleDescription(
      'Анимация показывает, как Kaigo анализирует исходный сайт и добавляет готовый AI-виджет.',
    );
  });
});
