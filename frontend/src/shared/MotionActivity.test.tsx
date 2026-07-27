import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const motionState = vi.hoisted(() => ({
  inView: true,
  reducedMotion: false,
}));

vi.mock('motion/react', () => ({
  useInView: () => motionState.inView,
  useReducedMotion: () => motionState.reducedMotion,
}));

import { MotionActivity } from './MotionActivity';

const setVisibility = (visibilityState: DocumentVisibilityState) => {
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    value: visibilityState,
  });
  act(() => document.dispatchEvent(new Event('visibilitychange')));
};

describe('MotionActivity', () => {
  beforeEach(() => {
    motionState.inView = true;
    motionState.reducedMotion = false;
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'visible',
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('is active only when in view, visible, and motion is allowed', () => {
    const { rerender } = render(
      <MotionActivity>{(active) => <span>{active ? 'active' : 'paused'}</span>}</MotionActivity>,
    );

    expect(screen.getByText('active').parentElement).toHaveAttribute('data-motion-active', 'true');

    motionState.inView = false;
    rerender(<MotionActivity>{(active) => <span>{active ? 'active' : 'paused'}</span>}</MotionActivity>);
    expect(screen.getByText('paused').parentElement).toHaveAttribute('data-motion-active', 'false');

    motionState.inView = true;
    motionState.reducedMotion = true;
    rerender(<MotionActivity>{(active) => <span>{active ? 'active' : 'paused'}</span>}</MotionActivity>);
    expect(screen.getByText('paused').parentElement).toHaveAttribute('data-motion-active', 'false');
  });

  it('pauses while the document is hidden and resumes when visible', () => {
    render(<MotionActivity><span>scene</span></MotionActivity>);

    setVisibility('hidden');
    expect(screen.getByText('scene').parentElement).toHaveAttribute('data-motion-active', 'false');

    setVisibility('visible');
    expect(screen.getByText('scene').parentElement).toHaveAttribute('data-motion-active', 'true');
  });

  it('registers one visibility listener and removes the same listener on cleanup', () => {
    const addEventListener = vi.spyOn(document, 'addEventListener');
    const removeEventListener = vi.spyOn(document, 'removeEventListener');
    const { unmount } = render(<MotionActivity><span>scene</span></MotionActivity>);

    const visibilityRegistrations = addEventListener.mock.calls.filter(([event]) => event === 'visibilitychange');
    expect(visibilityRegistrations).toHaveLength(1);

    unmount();
    expect(removeEventListener).toHaveBeenCalledWith(
      'visibilitychange',
      visibilityRegistrations[0][1],
    );
  });
});
