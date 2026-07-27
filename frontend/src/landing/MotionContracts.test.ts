import { describe, expect, it } from 'vitest';

import { WIDGET_LAUNCHER_REPEAT_DELAY_SECONDS } from '../shared/BrowserMockup';
import {
  CAPABILITY_CARD_STAGGER_SECONDS,
  CAPABILITY_REVEAL_DELAYS_SECONDS,
} from './CapabilitiesSection';
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
});
