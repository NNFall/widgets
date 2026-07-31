import { campaignFromSearch } from './campaign';

const ENTRY_DEADLINE_MS = 750;
let landingJourneyPromise: Promise<void> | null = null;

async function recordLandingJourney(search: string): Promise<void> {
  const controller = new AbortController();
  let timeoutId: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<void>((resolve) => {
    timeoutId = setTimeout(() => {
      controller.abort();
      resolve();
    }, ENTRY_DEADLINE_MS);
  });
  const request = fetch('/api/analytics/entry', {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    keepalive: true,
    body: JSON.stringify({ campaign: campaignFromSearch(search) }),
    signal: controller.signal,
  }).then(() => undefined, () => undefined);
  await Promise.race([request, deadline]);
  if (timeoutId !== undefined) clearTimeout(timeoutId);
}

export function ensureLandingJourney(
  search = window.location.search,
): Promise<void> {
  landingJourneyPromise ??= recordLandingJourney(search);
  return landingJourneyPromise;
}
