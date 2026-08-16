import type { FeedbackTopicId } from './contact';

export type FeedbackSource =
  | 'landing_contact'
  | 'studio_account'
  | 'studio_auth_error';

export type FeedbackSubmission = {
  topic: FeedbackTopicId;
  message: string;
  source: FeedbackSource;
  consent: {
    version: string;
    accepted: true;
  };
};

export class FeedbackApiError extends Error {
  readonly status: number;
  readonly retryAfter: number | null;

  constructor(message: string, status: number, retryAfter: number | null = null) {
    super(message);
    this.name = 'FeedbackApiError';
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

type JsonRecord = Record<string, unknown>;

function retryAfterSeconds(response: Response): number | null {
  const header = response.headers.get('Retry-After');
  if (header === null) return null;
  const seconds = Number(header);
  return Number.isFinite(seconds) ? seconds : null;
}

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

export async function fetchFeedbackSession(signal?: AbortSignal) {
  const response = await fetch('/api/feedback/session', {
    credentials: 'include',
    headers: { Accept: 'application/json' },
    signal,
  });
  if (!response.ok) {
    throw new FeedbackApiError(
      'feedback_session_failed',
      response.status,
      retryAfterSeconds(response),
    );
  }

  const payload = await readJson(response);
  if (
    !isRecord(payload)
    || typeof payload.csrf_token !== 'string'
    || typeof payload.consent_version !== 'string'
    || typeof payload.message_max_length !== 'number'
  ) {
    throw new FeedbackApiError(
      'invalid_feedback_session',
      response.status,
      retryAfterSeconds(response),
    );
  }

  return {
    csrfToken: payload.csrf_token,
    consentVersion: payload.consent_version,
    messageMaxLength: payload.message_max_length,
  };
}

export async function submitFeedback(input: {
  payload: FeedbackSubmission;
  csrfToken: string;
  idempotencyKey: string;
  signal?: AbortSignal;
}) {
  const response = await fetch('/api/feedback', {
    method: 'POST',
    credentials: 'include',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      'X-CSRF-Token': input.csrfToken,
      'Idempotency-Key': input.idempotencyKey,
    },
    body: JSON.stringify(input.payload),
    signal: input.signal,
  });

  if (response.status !== 200 && response.status !== 201) {
    throw new FeedbackApiError(
      'feedback_submit_failed',
      response.status,
      retryAfterSeconds(response),
    );
  }

  const payload = await readJson(response);
  if (
    !isRecord(payload)
    || payload.status !== 'stored'
    || typeof payload.receipt_id !== 'string'
    || typeof payload.received_at !== 'string'
  ) {
    throw new FeedbackApiError(
      'invalid_feedback_receipt',
      response.status,
      retryAfterSeconds(response),
    );
  }

  return {
    receiptId: payload.receipt_id,
    status: 'stored' as const,
    receivedAt: payload.received_at,
  };
}

export function createFeedbackIdempotencyKey(): string {
  return `feedback-${crypto.randomUUID()}`;
}
