import type { BuilderRunInput, BuilderRunSnapshot } from './types';

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
