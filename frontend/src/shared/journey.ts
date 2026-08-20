import { campaignFromSearch } from './campaign';

const ENTRY_DEADLINE_MS = 750;
type LandingResult = 'success' | 'rejected' | 'ambiguous';
let landingJourneyPromise: Promise<LandingResult> | null = null;
const journeyEventPromises = new Map<ClientJourneyEvent, Promise<boolean>>();
type PostResult = 'success' | 'rejected' | 'network_error';

export type ClientJourneyEvent =
  | 'landing_scrolled_end'
  | 'studio_cta_clicked'
  | 'studio_entered';

async function post(path: string, body: object): Promise<PostResult> {
  try {
    const response = await fetch(path, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      keepalive: true,
      body: JSON.stringify(body),
    });
    return response.ok ? 'success' : 'rejected';
  } catch {
    return 'network_error';
  }
}

async function recordLandingJourney(search: string): Promise<LandingResult> {
  const body = {
    campaign: campaignFromSearch(search),
  };
  const first = await post('/api/analytics/entry', body);
  if (first === 'success') return 'success';
  if (first !== 'rejected') return 'ambiguous';
  const second = await post('/api/analytics/entry', body);
  if (second === 'success') return 'success';
  return second === 'rejected' ? 'rejected' : 'ambiguous';
}

async function ensureLandingJourneyRecorded(
  search = window.location.search,
): Promise<boolean> {
  const request = landingJourneyPromise ?? recordLandingJourney(search);
  landingJourneyPromise = request;
  const result = await request;
  if (result === 'rejected' && landingJourneyPromise === request) {
    landingJourneyPromise = null;
  }
  return result === 'success';
}

export async function ensureLandingJourney(
  search = window.location.search,
): Promise<void> {
  let timeoutId: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<void>((resolve) => {
    timeoutId = setTimeout(resolve, ENTRY_DEADLINE_MS);
  });
  await Promise.race([
    ensureLandingJourneyRecorded(search).then(() => undefined),
    deadline,
  ]);
  if (timeoutId !== undefined) clearTimeout(timeoutId);
}

export async function recordJourneyEvent(
  eventType: ClientJourneyEvent,
): Promise<void> {
  const existing = journeyEventPromises.get(eventType);
  if (existing) {
    await existing;
    return;
  }
  const request = (async () => {
    if (!await ensureLandingJourneyRecorded()) return false;
    const body = { event_type: eventType };
    if (await post('/api/analytics/event', body) === 'success') return true;
    return (await post('/api/analytics/event', body)) === 'success';
  })();
  journeyEventPromises.set(eventType, request);
  const succeeded = await request;
  if (!succeeded && journeyEventPromises.get(eventType) === request) {
    journeyEventPromises.delete(eventType);
  }
}
