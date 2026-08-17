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
  honeypot?: string;
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
  const value = header.trim();
  if (!/^\d+$/.test(value)) return null;
  const seconds = Number(value);
  return Number.isSafeInteger(seconds) ? seconds : null;
}

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isNonBlankString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}

function isPositiveSafeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0;
}

function isUtcTimestamp(value: unknown): value is string {
  if (
    !isNonBlankString(value)
    || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|\+00:00)$/.test(value)
  ) {
    return false;
  }
  return Number.isFinite(Date.parse(value));
}

function isNamedError(value: unknown, name: string): boolean {
  return typeof value === 'object'
    && value !== null
    && 'name' in value
    && value.name === name;
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch (error) {
    if (isNamedError(error, 'AbortError')) throw error;
    if (isNamedError(error, 'SyntaxError')) return null;
    throw error;
  }
}

function toFeedbackPayload(payload: FeedbackSubmission): FeedbackSubmission {
  const transportPayload: FeedbackSubmission = {
    topic: payload.topic,
    message: payload.message,
    source: payload.source,
    consent: {
      version: payload.consent.version,
      accepted: true,
    },
  };

  if (typeof payload.honeypot === 'string') {
    transportPayload.honeypot = payload.honeypot;
  }

  return transportPayload;
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
    || !isNonBlankString(payload.csrf_token)
    || !isNonBlankString(payload.consent_version)
    || !isPositiveSafeInteger(payload.message_max_length)
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
    body: JSON.stringify(toFeedbackPayload(input.payload)),
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
    || !isNonBlankString(payload.receipt_id)
    || !isUtcTimestamp(payload.received_at)
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
