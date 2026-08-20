import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { recordJourneyEvent, ensureLandingJourney } = vi.hoisted(() => ({
  recordJourneyEvent: vi.fn().mockResolvedValue(undefined),
  ensureLandingJourney: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('../shared/journey', () => ({
  ensureLandingJourney,
  recordJourneyEvent,
}));

import { LandingPage } from './LandingPage';
import { App } from '../App';

describe('landing funnel milestones', () => {
  let observerCallback: IntersectionObserverCallback;
  let observedTarget: Element | null;
  const disconnect = vi.fn();

  beforeEach(() => {
    recordJourneyEvent.mockClear();
    ensureLandingJourney.mockClear();
    disconnect.mockClear();
    observedTarget = null;
    vi.stubGlobal('IntersectionObserver', class {
      constructor(callback: IntersectionObserverCallback) {
        observerCallback = callback;
      }

      observe(target: Element) { observedTarget = target; }
      unobserve() {}
      disconnect() {
        disconnect();
      }
      takeRecords() { return []; }
      readonly root = null;
      readonly rootMargin = '0px';
      readonly thresholds = [1];
    });
  });

  afterEach(() => {
    cleanup();
    window.history.replaceState({}, '', '/');
    vi.unstubAllGlobals();
  });

  it('records final-section visibility and Studio CTA clicks once', async () => {
    render(<LandingPage />);

    expect(ensureLandingJourney).toHaveBeenCalledOnce();
    const scrollEnd = document.getElementById('landing-scroll-end');
    expect(scrollEnd).not.toBeNull();
    expect(observedTarget).toBe(scrollEnd);
    expect(scrollEnd?.nextElementSibling).toHaveClass('site-footer');
    if (!scrollEnd) throw new Error('missing landing scroll-end marker');
    const rectangle = scrollEnd.getBoundingClientRect();
    const visibleEntry: IntersectionObserverEntry = {
      time: 0,
      target: scrollEnd,
      rootBounds: null,
      boundingClientRect: rectangle,
      intersectionRect: rectangle,
      isIntersecting: true,
      intersectionRatio: 1,
    };
    observerCallback(
      [{ ...visibleEntry, intersectionRatio: 0.5 }],
      {} as IntersectionObserver,
    );
    expect(recordJourneyEvent).not.toHaveBeenCalledWith('landing_scrolled_end');
    observerCallback(
      [visibleEntry],
      {} as IntersectionObserver,
    );
    observerCallback(
      [visibleEntry],
      {} as IntersectionObserver,
    );

    const studioLink = screen.getAllByRole('link', { name: 'Перейти в студию' })[0];
    studioLink.addEventListener('click', (event) => event.preventDefault(), { once: true });
    fireEvent.click(studioLink);

    await waitFor(() => {
      expect(recordJourneyEvent).toHaveBeenCalledWith('landing_scrolled_end');
      expect(recordJourneyEvent).toHaveBeenCalledWith('studio_cta_clicked');
    });
    expect(recordJourneyEvent.mock.calls.filter(
      ([eventType]) => eventType === 'landing_scrolled_end',
    )).toHaveLength(1);
    expect(disconnect).toHaveBeenCalled();
  });

  it('records a Studio entry on the application route', async () => {
    window.history.replaceState({}, '', '/studio');
    render(<App />);

    await waitFor(() => {
      expect(recordJourneyEvent).toHaveBeenCalledWith('studio_entered');
    });
  });
});
