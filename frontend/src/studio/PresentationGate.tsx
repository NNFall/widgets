import { ReactNode, useEffect, useState } from 'react';

import { StudioPresentation, type PresentationScenario } from './StudioPresentationMode';

const PRESENTATION_SCENARIOS = new Set<PresentationScenario>([
  'index',
  'publication-start',
  'founder-offer',
  'intro-no-renew',
  'intro-auto-renew',
  'all-plans',
  'payment-pending',
  'founder-active',
  'subscription-active',
  'published',
  'feedback',
  'tariff-limits',
]);

function requestedScenario(): PresentationScenario | null {
  const value = new URLSearchParams(window.location.search).get('presentation');
  return value && PRESENTATION_SCENARIOS.has(value as PresentationScenario)
    ? value as PresentationScenario
    : null;
}

export function PresentationGate({ children }: { children: ReactNode }) {
  const scenario = requestedScenario();
  const [allowed, setAllowed] = useState<boolean | null>(scenario ? null : false);

  useEffect(() => {
    if (!scenario) {
      setAllowed(false);
      return undefined;
    }
    const abort = new AbortController();
    void fetch('/api/operator/funnel', {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
      signal: abort.signal,
    })
      .then((response) => setAllowed(response.ok))
      .catch(() => {
        if (!abort.signal.aborted) setAllowed(false);
      });
    return () => abort.abort();
  }, [scenario]);

  if (!scenario || allowed === false) return children;
  if (allowed === null) {
    return <main className="auth-gate auth-gate--loading" role="status">Открываем студию…</main>;
  }
  return <StudioPresentation scenario={scenario} />;
}
