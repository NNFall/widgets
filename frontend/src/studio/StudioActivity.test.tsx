import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { BuilderEvent } from './types';

const motionState = vi.hoisted(() => ({ reducedMotion: false }));

vi.mock('motion/react', () => ({
  useReducedMotion: () => motionState.reducedMotion,
}));

import { StudioActivity } from './StudioActivity';

function studioEvent(overrides: Partial<BuilderEvent> = {}): BuilderEvent {
  return {
    run_id: 'run-1',
    sequence: 2,
    timestamp: '',
    type: 'stage.started',
    stage: 'identity',
    status: 'running',
    message: 'raw model bytes',
    revision: null,
    usage: { prompt_tokens: 0, output_tokens: 0, thinking_tokens: 0, total_tokens: 0 },
    issues: [],
    changes: [],
    error_code: null,
    ...overrides,
  };
}

describe('StudioActivity', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    motionState.reducedMotion = false;
  });

  afterEach(() => {
    cleanup();
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it('keeps a visible activity for two seconds and then shows the newest safe event', () => {
    const { rerender } = render(
      <StudioActivity running events={[]} stage="foundation" fallback="Начинаем работу" />,
    );

    rerender(
      <StudioActivity
        running
        events={[studioEvent()]}
        stage="identity"
        fallback="Продолжаем"
      />,
    );

    act(() => vi.advanceTimersByTime(1_999));
    expect(screen.getByRole('status')).toHaveTextContent('Начинаем работу');

    act(() => vi.advanceTimersByTime(1));
    expect(screen.getByRole('status')).toHaveTextContent('Настраиваем стиль под ваш бренд');
    expect(screen.queryByText('raw model bytes')).not.toBeInTheDocument();
  });

  it('replaces a pending burst with the newest truthful activity', () => {
    const { rerender } = render(
      <StudioActivity running events={[]} stage="foundation" fallback="Начинаем работу" />,
    );

    rerender(
      <StudioActivity
        running
        events={[studioEvent()]}
        stage="identity"
        fallback="Продолжаем"
      />,
    );
    rerender(
      <StudioActivity
        running
        events={[studioEvent(), studioEvent({
          sequence: 3,
          type: 'visual_audit.started',
          stage: 'validation',
        })]}
        stage="validation"
        fallback="Продолжаем"
      />,
    );

    act(() => vi.advanceTimersByTime(2_000));
    expect(screen.getByRole('status')).toHaveTextContent('Проверяем виджет на разных экранах');
  });

  it('clears a pending update on unmount', () => {
    const { rerender, unmount } = render(
      <StudioActivity running events={[]} stage="foundation" fallback="Начинаем работу" />,
    );
    rerender(
      <StudioActivity running events={[studioEvent()]} stage="identity" fallback="Продолжаем" />,
    );

    expect(vi.getTimerCount()).toBe(1);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('keeps truthful text and marks reduced motion without delaying semantics', () => {
    motionState.reducedMotion = true;
    render(<StudioActivity running events={[]} stage="foundation" fallback="Начинаем работу" />);

    expect(screen.getByRole('status')).toHaveTextContent('Начинаем работу');
    expect(screen.getByRole('status')).toHaveAttribute('data-motion', 'reduced');
  });
});
