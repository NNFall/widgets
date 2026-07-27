import { describe, expect, it } from 'vitest';

import { WIDGET_LAUNCHER_REPEAT_DELAY_SECONDS } from '../shared/BrowserMockup';
import stylesSource from '../styles.css?raw';
import {
  CAPABILITY_CARD_STAGGER_SECONDS,
  CAPABILITY_REVEAL_DELAYS_SECONDS,
} from './CapabilitiesSection';
import heroOrbitSceneSource from './HeroOrbitScene.tsx?raw';
import { HOW_CARD_STAGGER_SECONDS } from './HowItWorksSection';

describe('landing motion contracts', () => {
  it('staggers narrative and capability cards at a legible 260ms cadence', () => {
    expect(HOW_CARD_STAGGER_SECONDS).toBe(0.26);
    expect(CAPABILITY_CARD_STAGGER_SECONDS).toBe(0.26);
  });

  it('reveals all six capability cards in one alternating sequence without pairs', () => {
    expect(CAPABILITY_REVEAL_DELAYS_SECONDS).toEqual([0, 0.26, 0.52, 0.78, 1.04, 1.3]);
    expect(new Set(CAPABILITY_REVEAL_DELAYS_SECONDS).size).toBe(6);
  });

  it('keeps the recurring launcher animation on a restrained 13 second cycle', () => {
    const pulseDurationSeconds = 2.3;
    const totalCycleSeconds = pulseDurationSeconds + WIDGET_LAUNCHER_REPEAT_DELAY_SECONDS;

    expect(totalCycleSeconds).toBeGreaterThanOrEqual(12);
    expect(totalCycleSeconds).toBeLessThanOrEqual(15);
    expect(totalCycleSeconds).toBe(13);
  });

  it('matches scanner travel to the distinct cinematic and loop scanning phases', () => {
    expect(heroOrbitSceneSource).toMatch(
      /cinematic:\s*6\.3,\s*\n\s*loop:\s*3\.8,/,
    );
    expect(heroOrbitSceneSource).toMatch(
      /duration:\s*reducedMotion\s*\?\s*0\s*:\s*SCANNER_DURATION_SECONDS\[program\]/,
    );
  });

  it('keeps every new CSS motion keyframe on transform and opacity only', () => {
    const motionKeyframes = [
      'hero-scanner-particle',
      'hero-widget-halo',
      'hero-widget-burst',
      'hero-rest-shimmer',
    ];

    motionKeyframes.forEach((name) => {
      const body = stylesSource.match(new RegExp(`@keyframes ${name}\\s*\\{([\\s\\S]*?)\\n\\}`))?.[1];
      expect(body, `missing @keyframes ${name}`).toBeDefined();
      expect(body).toMatch(/(?:transform|opacity):/);
      expect(body).not.toMatch(/(?:top|right|bottom|left|width|height|filter|box-shadow):/);
    });
  });

  it('explicitly disables every new infinite hero animation for reduced motion', () => {
    const reducedMotionStart = stylesSource.indexOf('@media (prefers-reduced-motion: reduce)');
    const nextMediaQuery = stylesSource.indexOf('@media (max-width: 1280px)', reducedMotionStart);
    const reducedMotionRules = stylesSource.slice(reducedMotionStart, nextMediaQuery);

    expect(reducedMotionRules).toContain('.hero-browser-stage__scanner-particle');
    expect(reducedMotionRules).toContain('.widget-preview__shimmer');
    expect(reducedMotionRules).toContain('animation: none !important;');
    expect(heroOrbitSceneSource).toMatch(/motionComplete\s*&&\s*!reducedMotion\s*\?\s*'rest'/);
  });
});
