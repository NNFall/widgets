import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

describe('ensureLandingJourney', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('coalesces requests and sends only allowlisted campaign values', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchMock);
    const { ensureLandingJourney } = await import('./journey');

    await Promise.all([
      ensureLandingJourney('?utm_source=telegram&utm_campaign=launch&email=private%40example.com'),
      ensureLandingJourney('?utm_source=telegram&utm_campaign=launch&email=private%40example.com'),
    ]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith('/api/analytics/entry', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      keepalive: true,
      body: JSON.stringify({
        campaign: { utm_source: 'telegram', utm_campaign: 'launch' },
      }),
      signal: expect.any(AbortSignal),
    });
    expect(JSON.stringify(fetchMock.mock.calls)).not.toContain('private@example.com');
  });

  it('swallows non-success responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 500 })));
    const { ensureLandingJourney } = await import('./journey');
    await expect(ensureLandingJourney('?utm_source=telegram')).resolves.toBeUndefined();
  });

  it('resolves after the bounded deadline when transport never settles', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => undefined)));
    const { ensureLandingJourney } = await import('./journey');
    const request = ensureLandingJourney('?utm_source=telegram');
    await vi.advanceTimersByTimeAsync(751);
    await expect(request).resolves.toBeUndefined();
  });
});
