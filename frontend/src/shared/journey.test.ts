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
    });
    expect(JSON.stringify(fetchMock.mock.calls)).not.toContain('private@example.com');
  });

  it('swallows a non-success response but retries first-touch attribution', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 500 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchMock);
    const { ensureLandingJourney } = await import('./journey');
    await expect(ensureLandingJourney('?utm_source=telegram')).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('resolves after the bounded deadline when transport never settles', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(() => new Promise<Response>(() => undefined));
    vi.stubGlobal('fetch', fetchMock);
    const { ensureLandingJourney } = await import('./journey');
    const request = ensureLandingJourney('?utm_source=telegram');
    await vi.advanceTimersByTimeAsync(751);
    await expect(request).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledOnce();
    const [, requestInit] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(requestInit).not.toHaveProperty('signal');
  });

  it('keeps one slow entry request authoritative after the UI deadline', async () => {
    vi.useFakeTimers();
    let resolveEntry: ((response: Response) => void) | undefined;
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => new Promise<Response>((resolve) => {
        resolveEntry = resolve;
      }))
      .mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchMock);
    const { ensureLandingJourney, recordJourneyEvent } = await import('./journey');

    const entry = ensureLandingJourney('?utm_source=yandex&utm_medium=cpc');
    await vi.advanceTimersByTimeAsync(751);
    await entry;
    resolveEntry?.(new Response(null, { status: 204 }));
    await vi.runAllTimersAsync();
    await recordJourneyEvent('studio_entered');

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      '/api/analytics/entry',
      '/api/analytics/event',
    ]);
  });

  it('does not duplicate an entry after an ambiguous network failure', async () => {
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError('network'));
    vi.stubGlobal('fetch', fetchMock);
    const { ensureLandingJourney, recordJourneyEvent } = await import('./journey');

    await ensureLandingJourney('?utm_source=yandex&utm_medium=cpc');
    await recordJourneyEvent('studio_entered');

    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it('records each allowlisted browser milestone once without metadata', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchMock);
    const { recordJourneyEvent } = await import('./journey');

    await Promise.all([
      recordJourneyEvent('landing_scrolled_end'),
      recordJourneyEvent('landing_scrolled_end'),
      recordJourneyEvent('studio_cta_clicked'),
      recordJourneyEvent('studio_entered'),
    ]);

    const eventCalls = fetchMock.mock.calls.filter(([url]) => url === '/api/analytics/event');
    expect(eventCalls).toHaveLength(3);
    expect(eventCalls.map(([, init]) => JSON.parse(String((init as RequestInit).body)))).toEqual([
      { event_type: 'landing_scrolled_end' },
      { event_type: 'studio_cta_clicked' },
      { event_type: 'studio_entered' },
    ]);
    expect(JSON.stringify(eventCalls)).not.toMatch(/ip|email|url|metadata/i);
  });

  it('retries entry once before recording a milestone without losing attribution', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 500 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchMock);
    const { recordJourneyEvent } = await import('./journey');

    await recordJourneyEvent('studio_entered');
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      '/api/analytics/entry',
      '/api/analytics/entry',
      '/api/analytics/event',
    ]);
  });

  it('retries a one-shot milestone request once', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response(null, { status: 500 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchMock);
    const { recordJourneyEvent } = await import('./journey');

    await recordJourneyEvent('landing_scrolled_end');

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      '/api/analytics/entry',
      '/api/analytics/event',
      '/api/analytics/event',
    ]);
  });
});
