import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useHeroMotionCycle } from './useHeroMotionCycle';

type ReducedMotionListener = (event: { matches: boolean }) => void;

const installReducedMotionPreference = (initialMatches: boolean) => {
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
    listenerCount: () => listeners.size,
  };
};

const installLegacyReducedMotionPreference = (initialMatches: boolean) => {
  const matches = initialMatches;
  const listeners = new Set<ReducedMotionListener>();
  const mediaQuery = {
    get matches() {
      return matches;
    },
    media: '(prefers-reduced-motion: reduce)',
    onchange: null,
    addListener: vi.fn((listener: ReducedMotionListener) => listeners.add(listener)),
    removeListener: vi.fn((listener: ReducedMotionListener) => listeners.delete(listener)),
    dispatchEvent: vi.fn(),
  };

  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn().mockImplementation(() => mediaQuery),
  });

  return {
    mediaQuery,
    listenerCount: () => listeners.size,
  };
};

const advance = (milliseconds: number) => {
  act(() => vi.advanceTimersByTime(milliseconds));
};

describe('useHeroMotionCycle', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    installReducedMotionPreference(false);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it('plays the cinematic once, then builds every loop in 5.4 seconds', () => {
    const { result } = renderHook(() => useHeroMotionCycle());

    expect(result.current).toMatchObject({
      program: 'cinematic',
      phase: 'source',
      cycle: 0,
      visibleCards: 0,
    });
    expect(vi.getTimerCount()).toBe(1);

    advance(1_200);
    expect(result.current.phase).toBe('scanning');
    advance(1_200);
    expect(result.current.visibleCards).toBe(1);
    advance(1_600);
    expect(result.current.visibleCards).toBe(2);
    advance(1_600);
    expect(result.current.visibleCards).toBe(3);
    advance(1_900);
    expect(result.current.phase).toBe('widget');
    advance(900);
    expect(result.current.phase).toBe('complete');

    advance(11_999);
    expect(result.current.phase).toBe('complete');
    advance(1);
    expect(result.current).toMatchObject({ program: 'cinematic', phase: 'resetting' });

    advance(600);
    expect(result.current).toMatchObject({ program: 'loop', phase: 'source', cycle: 1 });

    advance(800);
    expect(result.current.phase).toBe('scanning');
    advance(800);
    expect(result.current.visibleCards).toBe(1);
    advance(1_000);
    expect(result.current.visibleCards).toBe(2);
    advance(1_000);
    expect(result.current.visibleCards).toBe(3);
    advance(1_000);
    expect(result.current.phase).toBe('widget');
    advance(800);
    expect(result.current.phase).toBe('complete');
  });

  it('keeps exactly one scheduled transition across repeated loops and clears it on unmount', () => {
    const { result, unmount } = renderHook(() => useHeroMotionCycle());

    const advanceAndExpectOneTimer = (milliseconds: number) => {
      advance(milliseconds);
      expect(vi.getTimerCount()).toBe(1);
    };

    for (const duration of [1_200, 1_200, 1_600, 1_600, 1_900, 900, 12_000, 600]) {
      advanceAndExpectOneTimer(duration);
    }

    for (let expectedCycle = 2; expectedCycle <= 3; expectedCycle += 1) {
      for (const duration of [800, 800, 1_000, 1_000, 1_000, 800, 11_000, 600]) {
        advanceAndExpectOneTimer(duration);
      }
      expect(result.current).toMatchObject({ program: 'loop', cycle: expectedCycle });
    }

    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('cancels the active transition when reduced motion is enabled', () => {
    const preference = installReducedMotionPreference(false);
    const { result, unmount } = renderHook(() => useHeroMotionCycle());

    expect(preference.listenerCount()).toBe(1);
    expect(vi.getTimerCount()).toBe(1);

    act(() => preference.setMatches(true));

    expect(result.current).toMatchObject({ phase: 'complete', visibleCards: 3 });
    expect(vi.getTimerCount()).toBe(0);

    advance(60_000);
    expect(result.current).toMatchObject({ program: 'cinematic', cycle: 0 });

    unmount();
    expect(preference.listenerCount()).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('supports legacy matchMedia listeners and removes them on unmount', () => {
    const preference = installLegacyReducedMotionPreference(false);
    let unmount: (() => void) | undefined;

    expect(() => {
      ({ unmount } = renderHook(() => useHeroMotionCycle()));
    }).not.toThrow();
    expect(preference.mediaQuery.addListener).toHaveBeenCalledOnce();
    expect(preference.listenerCount()).toBe(1);
    expect(vi.getTimerCount()).toBe(1);

    unmount?.();
    expect(preference.mediaQuery.removeListener).toHaveBeenCalledOnce();
    expect(preference.listenerCount()).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
  });
});
