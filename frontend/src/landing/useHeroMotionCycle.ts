import { useEffect, useState } from 'react';

export type HeroMotionProgram = 'cinematic' | 'loop';
export type HeroMotionPhase = 'source' | 'scanning' | 'widget' | 'complete' | 'resetting';
export type HeroVisibleCards = 0 | 1 | 2 | 3;

export interface HeroMotionCycleState {
  program: HeroMotionProgram;
  phase: HeroMotionPhase;
  cycle: number;
  visibleCards: HeroVisibleCards;
}

interface ScheduledTransition {
  delay: number;
  next: HeroMotionCycleState;
}

const initialCinematicState: HeroMotionCycleState = {
  program: 'cinematic',
  phase: 'source',
  cycle: 0,
  visibleCards: 0,
};

const reducedMotionState: HeroMotionCycleState = {
  ...initialCinematicState,
  phase: 'complete',
  visibleCards: 3,
};

function reducedMotionRequested() {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function cinematicTransition(state: HeroMotionCycleState): ScheduledTransition {
  if (state.phase === 'source') {
    return { delay: 1_200, next: { ...state, phase: 'scanning' } };
  }

  if (state.phase === 'scanning') {
    if (state.visibleCards === 0) {
      return { delay: 1_200, next: { ...state, visibleCards: 1 } };
    }
    if (state.visibleCards === 1) {
      return { delay: 1_600, next: { ...state, visibleCards: 2 } };
    }
    if (state.visibleCards === 2) {
      return { delay: 1_600, next: { ...state, visibleCards: 3 } };
    }
    return { delay: 1_900, next: { ...state, phase: 'widget' } };
  }

  if (state.phase === 'widget') {
    return { delay: 900, next: { ...state, phase: 'complete' } };
  }

  if (state.phase === 'complete') {
    return {
      delay: 12_000,
      next: { ...state, phase: 'resetting', visibleCards: 0 },
    };
  }

  return {
    delay: 600,
    next: {
      program: 'loop',
      phase: 'source',
      cycle: state.cycle + 1,
      visibleCards: 0,
    },
  };
}

function loopTransition(state: HeroMotionCycleState): ScheduledTransition {
  if (state.phase === 'source') {
    return { delay: 800, next: { ...state, phase: 'scanning' } };
  }

  if (state.phase === 'scanning') {
    if (state.visibleCards === 0) {
      return { delay: 800, next: { ...state, visibleCards: 1 } };
    }
    if (state.visibleCards === 1) {
      return { delay: 1_000, next: { ...state, visibleCards: 2 } };
    }
    if (state.visibleCards === 2) {
      return { delay: 1_000, next: { ...state, visibleCards: 3 } };
    }
    return { delay: 1_000, next: { ...state, phase: 'widget' } };
  }

  if (state.phase === 'widget') {
    return { delay: 800, next: { ...state, phase: 'complete' } };
  }

  if (state.phase === 'complete') {
    return {
      delay: 11_000,
      next: { ...state, phase: 'resetting', visibleCards: 0 },
    };
  }

  return {
    delay: 600,
    next: {
      ...state,
      phase: 'source',
      cycle: state.cycle + 1,
      visibleCards: 0,
    },
  };
}

function getNextTransition(state: HeroMotionCycleState) {
  return state.program === 'cinematic'
    ? cinematicTransition(state)
    : loopTransition(state);
}

export function useHeroMotionCycle() {
  const [reducedMotion, setReducedMotion] = useState(reducedMotionRequested);
  const [motionState, setMotionState] = useState<HeroMotionCycleState>(() =>
    reducedMotion ? reducedMotionState : initialCinematicState,
  );

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return;

    const mediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    const handleChange = (event: MediaQueryListEvent) => setReducedMotion(event.matches);

    setReducedMotion(mediaQuery.matches);
    mediaQuery.addEventListener('change', handleChange);
    return () => mediaQuery.removeEventListener('change', handleChange);
  }, []);

  useEffect(() => {
    if (reducedMotion) {
      setMotionState((current) => {
        if (current.phase === 'complete' && current.visibleCards === 3) return current;
        return { ...current, phase: 'complete', visibleCards: 3 };
      });
      return;
    }

    const transition = getNextTransition(motionState);
    const timer = window.setTimeout(() => setMotionState(transition.next), transition.delay);

    return () => window.clearTimeout(timer);
  }, [motionState, reducedMotion]);

  return { ...motionState, reducedMotion };
}
