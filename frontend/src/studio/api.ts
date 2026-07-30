import type { Campaign } from '../shared/campaign';
import type {
  AuthSessionSnapshot,
  BillingCheckout,
  BillingPayment,
  PendingBillingCheckout,
  BillingSubscription,
  BuilderRunInput,
  BuilderRunSnapshot,
  SaasEvent,
  SaasProject,
  SaasRunSnapshot,
} from './types';

const configuredBase = import.meta.env.VITE_BUILDER_BASE_URL ?? '/builder/';
const BUILDER_BASE = configuredBase.replace(/\/?$/, '/');

export class BuilderApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly raw: string;
  readonly retryable: boolean;

  constructor(message: string, options: {
    status: number;
    code: string | null;
    raw: string;
    retryable?: boolean;
  }) {
    super(message);
    this.name = 'BuilderApiError';
    this.status = options.status;
    this.code = options.code;
    this.raw = options.raw;
    this.retryable = options.retryable ?? false;
  }
}

export interface PublicationRelease {
  publication_id: string;
  release_id: string;
  artifact_id: string;
  stable_key: string;
  revision: number;
  allowed_domains: string[];
  checksum: string;
  embed_url: string;
  runtime_url: string;
}

export interface PublishProjectInput {
  artifact_id: string;
  revision?: number;
  allowed_domains?: string[];
}

export interface PublicationHistoryRelease {
  release_id: string;
  artifact_id: string;
  previous_release_id: string | null;
  revision: number;
  checksum: string;
  created_at: string;
}

export interface ProjectPublicationState {
  publication_id: string;
  stable_key: string;
  state: string;
  allowed_domains: string[];
  embed_url: string;
  runtime_url: string;
  active_release: PublicationHistoryRelease;
  releases: PublicationHistoryRelease[];
}

export function builderUrl(path: string) {
  const base = new URL(BUILDER_BASE, window.location.origin);
  return new URL(path.replace(/^\/+/, ''), base).toString();
}

async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(builderUrl(path), {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init.headers,
    },
  });
  const payload = await response.json().catch(() => ({})) as {
    error?: { code?: string; message?: string; retryable?: boolean };
  } & T;
  if (!response.ok) {
    const message = payload.error?.message || `HTTP ${response.status}`;
    throw new BuilderApiError(message, {
      status: response.status,
      code: payload.error?.code ?? null,
      raw: message,
      retryable: payload.error?.retryable ?? false,
    });
  }
  return payload;
}

async function saasRequestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...init.headers,
    },
  });
  const payload = await response.json().catch(() => ({})) as {
    error?: string | { code?: string; message?: string; retryable?: boolean };
  } & T;
  if (!response.ok) {
    const structured = typeof payload.error === 'object' ? payload.error : null;
    const raw = structured?.message || structured?.code || (typeof payload.error === 'string' ? payload.error : `HTTP ${response.status}`);
    throw new BuilderApiError(raw, {
      status: response.status,
      code: structured?.code ?? null,
      raw,
      retryable: structured?.retryable ?? response.status >= 500,
    });
  }
  return payload;
}

export function getAuthSession() {
  return saasRequestJson<AuthSessionSnapshot>('/api/auth/session');
}

export function createBillingCheckout(
  planCode: string,
  csrfToken: string,
  idempotencyKey: string,
  projectId: string,
  autoRenew = false,
) {
  return saasRequestJson<BillingCheckout>('/api/billing/checkout', {
    method: 'POST',
    headers: {
      'Idempotency-Key': idempotencyKey,
      'X-CSRF-Token': csrfToken,
    },
    body: JSON.stringify({
      plan_code: planCode,
      project_id: projectId,
      auto_renew: autoRenew,
    }),
  });
}

export function getBillingPayment(paymentId: string, signal?: AbortSignal) {
  return saasRequestJson<{ payment: BillingPayment }>(
    `/api/billing/payments/${encodeURIComponent(paymentId)}`,
    { signal },
  );
}

export function getPendingBillingPayment(signal?: AbortSignal) {
  return saasRequestJson<PendingBillingCheckout>(
    '/api/billing/payments/pending',
    { signal },
  );
}

export function resumeBillingPayment(paymentId: string, csrfToken: string) {
  return saasRequestJson<BillingCheckout>(
    `/api/billing/payments/${encodeURIComponent(paymentId)}/resume`,
    {
      method: 'POST',
      headers: { 'X-CSRF-Token': csrfToken },
    },
  );
}

export function getBillingSubscription(signal?: AbortSignal) {
  return saasRequestJson<{ subscription: BillingSubscription | null }>(
    '/api/billing/subscription',
    { signal },
  );
}

export function disableBillingAutoRenew(
  subscriptionId: string,
  csrfToken: string,
) {
  return saasRequestJson<{ subscription: BillingSubscription }>(
    `/api/billing/subscriptions/${encodeURIComponent(subscriptionId)}/auto-renew/off`,
    {
      method: 'POST',
      headers: { 'X-CSRF-Token': csrfToken },
    },
  );
}

export function getProject(projectId: string) {
  return saasRequestJson<SaasProject>(`/api/projects/${encodeURIComponent(projectId)}`);
}

export function getProjectPublication(projectId: string, signal?: AbortSignal) {
  return saasRequestJson<{ publication: ProjectPublicationState | null }>(
    `/api/projects/${encodeURIComponent(projectId)}/publication`,
    { signal },
  );
}

export function publishProject(
  projectId: string,
  input: PublishProjectInput,
  csrfToken: string,
) {
  return saasRequestJson<PublicationRelease>(
    `/api/projects/${encodeURIComponent(projectId)}/publish`,
    {
      method: 'POST',
      headers: { 'X-CSRF-Token': csrfToken },
      body: JSON.stringify(input),
    },
  );
}

export function rollbackPublication(
  publicationId: string,
  targetReleaseId: string,
  csrfToken: string,
) {
  return saasRequestJson<PublicationRelease>(
    `/api/publications/${encodeURIComponent(publicationId)}/rollback`,
    {
      method: 'POST',
      headers: { 'X-CSRF-Token': csrfToken },
      body: JSON.stringify({ target_release_id: targetReleaseId }),
    },
  );
}

export function createProject(
  sourceUrl: string,
  brief: string,
  csrfToken: string,
  campaign: Campaign = {},
) {
  return saasRequestJson<SaasProject>('/api/projects', {
    method: 'POST',
    headers: { 'X-CSRF-Token': csrfToken },
    body: JSON.stringify({
      url: sourceUrl,
      brief,
      ...(Object.keys(campaign).length > 0 ? { campaign } : {}),
    }),
  });
}

export function updateProjectDraft(
  projectId: string,
  sourceUrl: string,
  brief: string,
  csrfToken: string,
) {
  return saasRequestJson<SaasProject>(`/api/projects/${encodeURIComponent(projectId)}`, {
    method: 'PATCH',
    headers: { 'X-CSRF-Token': csrfToken },
    body: JSON.stringify({ url: sourceUrl, brief }),
  });
}

export function createProjectRun(projectId: string, csrfToken: string, idempotencyKey: string) {
  return saasRequestJson<SaasRunSnapshot>(`/api/projects/${encodeURIComponent(projectId)}/runs`, {
    method: 'POST',
    headers: {
      'Idempotency-Key': idempotencyKey,
      'X-CSRF-Token': csrfToken,
    },
    body: JSON.stringify({ mode: 'express' }),
  });
}

export function getProjectRun(runId: string, signal?: AbortSignal) {
  return saasRequestJson<SaasRunSnapshot>(`/api/runs/${encodeURIComponent(runId)}`, { signal });
}

export function cancelProjectRun(runId: string, csrfToken: string) {
  return saasRequestJson<{ run_id: string; cancel_requested: boolean; status: string }>(
    `/api/runs/${encodeURIComponent(runId)}/cancel`,
    {
      method: 'POST',
      headers: { 'X-CSRF-Token': csrfToken },
      body: '{}',
    },
  );
}

export function retryProjectRun(
  runId: string,
  csrfToken: string,
  idempotencyKey: string,
) {
  return saasRequestJson<SaasRunSnapshot>(`/api/runs/${encodeURIComponent(runId)}/retry`, {
    method: 'POST',
    headers: {
      'Idempotency-Key': idempotencyKey,
      'X-CSRF-Token': csrfToken,
    },
    body: '{}',
  });
}

export async function streamProjectRunEvents(
  runId: string,
  afterSequence: number,
  onEvent: (event: SaasEvent) => void,
  signal: AbortSignal,
) {
  const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/events`, {
    credentials: 'include',
    headers: afterSequence > 0 ? { 'Last-Event-ID': String(afterSequence) } : {},
    signal,
  });
  if (!response.ok) {
    throw new BuilderApiError(`HTTP ${response.status}`, {
      status: response.status,
      code: null,
      raw: `HTTP ${response.status}`,
      retryable: response.status >= 500,
    });
  }
  if (!response.body) throw new Error('SSE response body is unavailable');

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  const consume = (block: string) => {
    if (!block || block.startsWith(':')) return;
    const data = block
      .split('\n')
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trimStart())
      .join('\n');
    if (!data) return;
    const event = JSON.parse(data) as SaasEvent;
    if (Number.isInteger(event.sequence)) onEvent(event);
  };

  while (!signal.aborted) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n');
    let boundary = buffer.indexOf('\n\n');
    while (boundary >= 0) {
      consume(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf('\n\n');
    }
    if (done) {
      consume(buffer);
      break;
    }
  }
}

export function createBuilderRun(input: BuilderRunInput) {
  return requestJson<BuilderRunSnapshot>('api/runs', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function getBuilderRun(runId: string) {
  return requestJson<BuilderRunSnapshot>(`api/runs/${encodeURIComponent(runId)}`);
}

export function cancelBuilderRun(runId: string) {
  return requestJson<{ run_id: string; cancel_requested: boolean }>(
    `api/runs/${encodeURIComponent(runId)}/cancel`,
    { method: 'POST', body: '{}' },
  );
}

export function retryBuilderRun(runId: string) {
  return requestJson<BuilderRunSnapshot>(`api/runs/${encodeURIComponent(runId)}/retry`, {
    method: 'POST',
    body: '{}',
  });
}

export function refineBuilderRun(runId: string, message: string) {
  return requestJson<BuilderRunSnapshot>(`api/runs/${encodeURIComponent(runId)}/refine`, {
    method: 'POST',
    body: JSON.stringify({ message }),
  });
}

export function sendPreviewChat(
  runId: string,
  payload: { request_id: string; message: string; revision: number },
) {
  return requestJson<{ request_id: string; reply: string }>(
    `api/runs/${encodeURIComponent(runId)}/chat`,
    {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-Kaigo-Chat': 'v2' },
      body: JSON.stringify(payload),
    },
  );
}

export function saasPreviewUrl(runId: string, revision: number, channel: string) {
  const params = new URLSearchParams({
    revision: String(revision),
    channel,
  });
  return new URL(
    `/api/runs/${encodeURIComponent(runId)}/preview/document?${params.toString()}`,
    window.location.origin,
  ).toString();
}

export function sendSaasPreviewChat(
  runId: string,
  csrfToken: string,
  payload: { request_id: string; message: string; revision: number },
) {
  return saasRequestJson<{ request_id: string; reply: string }>(
    `/api/runs/${encodeURIComponent(runId)}/chat`,
    {
      method: 'POST',
      headers: { 'X-CSRF-Token': csrfToken },
      body: JSON.stringify(payload),
    },
  );
}
