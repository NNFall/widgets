import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  createFeedbackIdempotencyKey,
  fetchFeedbackSession,
  FeedbackApiError,
  submitFeedback,
  type FeedbackSubmission,
} from './feedbackApi';

const input: {
  payload: FeedbackSubmission;
  csrfToken: string;
  idempotencyKey: string;
} = {
  payload: {
    topic: 'improvement',
    message: 'Добавьте возможность сохранять черновик.',
    source: 'landing_contact',
    consent: { version: 'feedback-v2', accepted: true },
  },
  csrfToken: 'csrf-feedback',
  idempotencyKey: 'feedback-test-key',
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('feedback API session contract', () => {
  it('loads the CSRF and consent metadata with an optional abort signal', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      csrf_token: 'csrf-feedback',
      consent_version: 'feedback-v2',
      message_max_length: 4000,
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(fetchFeedbackSession()).resolves.toEqual({
      csrfToken: 'csrf-feedback',
      consentVersion: 'feedback-v2',
      messageMaxLength: 4000,
    });
    expect(fetchMock).toHaveBeenCalledWith('/api/feedback/session', {
      credentials: 'include',
      headers: { Accept: 'application/json' },
      signal: undefined,
    });
  });

  it.each([
    { csrf_token: 42, consent_version: 'feedback-v2', message_max_length: 4000 },
    { csrf_token: 'csrf-feedback', consent_version: null, message_max_length: 4000 },
    { csrf_token: 'csrf-feedback', consent_version: 'feedback-v2', message_max_length: '4000' },
    { csrf_token: '', consent_version: 'feedback-v2', message_max_length: 4000 },
    { csrf_token: '  ', consent_version: 'feedback-v2', message_max_length: 4000 },
    { csrf_token: 'csrf-feedback', consent_version: '', message_max_length: 4000 },
    { csrf_token: 'csrf-feedback', consent_version: '  ', message_max_length: 4000 },
    { csrf_token: 'csrf-feedback', consent_version: 'feedback-v2', message_max_length: 0 },
    { csrf_token: 'csrf-feedback', consent_version: 'feedback-v2', message_max_length: -1 },
    { csrf_token: 'csrf-feedback', consent_version: 'feedback-v2', message_max_length: 1.5 },
    {
      csrf_token: 'csrf-feedback',
      consent_version: 'feedback-v2',
      message_max_length: Number.MAX_SAFE_INTEGER + 1,
    },
  ])('rejects a malformed session payload', async (payload) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), { status: 200 })));

    await expect(fetchFeedbackSession()).rejects.toMatchObject({
      name: 'FeedbackApiError',
      message: 'invalid_feedback_session',
      status: 200,
    });
  });

  it('rethrows an AbortError raised while reading the session body', async () => {
    const controller = new AbortController();
    const response = new Response('{}', { status: 200 });
    const abortError = new DOMException('session body read aborted', 'AbortError');
    vi.spyOn(response, 'json').mockRejectedValue(abortError);
    const fetchMock = vi.fn().mockResolvedValue(response);
    vi.stubGlobal('fetch', fetchMock);

    await expect(fetchFeedbackSession(controller.signal)).rejects.toBe(abortError);
    expect(fetchMock).toHaveBeenCalledWith('/api/feedback/session', expect.objectContaining({
      signal: controller.signal,
    }));
  });
});

describe('feedback API submission contract', () => {
  it('submits only the stored-feedback payload and normalizes a valid receipt', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      receipt_id: 'fb_receipt',
      status: 'stored',
      received_at: '2026-08-17T10:00:00Z',
    }), {
      status: 201,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(submitFeedback(input)).resolves.toEqual({
      receiptId: 'fb_receipt',
      status: 'stored',
      receivedAt: '2026-08-17T10:00:00Z',
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/feedback');
    expect(init.method).toBe('POST');
    expect(init.credentials).toBe('include');
    const headers = new Headers(init.headers);
    expect(headers.get('Content-Type')).toBe('application/json');
    expect(headers.get('Accept')).toBe('application/json');
    expect(headers.get('X-CSRF-Token')).toBe('csrf-feedback');
    expect(headers.get('Idempotency-Key')).toBe('feedback-test-key');

    const body = JSON.parse(String(init.body)) as Record<string, unknown>;
    expect(body).toEqual(input.payload);
    for (const forbiddenField of ['email', 'contact', 'name', 'project_id', 'run_id', 'user_id']) {
      expect(body).not.toHaveProperty(forbiddenField);
    }
  });

  it('deep-whitelists the JSON body even when runtime input contains unknown fields', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      receipt_id: 'fb_receipt-whitelist',
      status: 'stored',
      received_at: '2026-08-17T10:00:00Z',
    }), { status: 201 }));
    vi.stubGlobal('fetch', fetchMock);

    const payloadWithUnknownFields = {
      ...input.payload,
      email: 'person@example.com',
      contact: '+79990000000',
      name: 'Не отправлять',
      project_id: 'project-123',
      run_id: 'run-123',
      user_id: 'user-123',
      consent: {
        version: 'feedback-v2',
        accepted: false,
        nested_extra: 'drop-me',
      },
    } as unknown as FeedbackSubmission;

    await expect(submitFeedback({ ...input, payload: payloadWithUnknownFields })).resolves.toMatchObject({
      receiptId: 'fb_receipt-whitelist',
      status: 'stored',
    });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({
      topic: 'improvement',
      message: 'Добавьте возможность сохранять черновик.',
      source: 'landing_contact',
      consent: { version: 'feedback-v2', accepted: true },
    });
  });

  it('whitelists the optional public honeypot without accepting any other runtime fields', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      receipt_id: 'fb_receipt-honeypot',
      status: 'stored',
      received_at: '2026-08-17T10:00:00Z',
    }), { status: 201 }));
    vi.stubGlobal('fetch', fetchMock);

    const payloadWithHoneypot = {
      ...input.payload,
      honeypot: 'bot signal',
      email: 'person@example.com',
      metadata: { should_not: 'ship' },
    } as unknown as FeedbackSubmission;

    await expect(submitFeedback({ ...input, payload: payloadWithHoneypot })).resolves.toMatchObject({
      receiptId: 'fb_receipt-honeypot',
      status: 'stored',
    });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({
      topic: 'improvement',
      message: 'Добавьте возможность сохранять черновик.',
      source: 'landing_contact',
      consent: { version: 'feedback-v2', accepted: true },
      honeypot: 'bot signal',
    });
    expect(JSON.parse(String(init.body))).not.toHaveProperty('email');
    expect(JSON.parse(String(init.body))).not.toHaveProperty('metadata');
  });

  it('accepts HTTP 200 as a stored receipt too', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      receipt_id: 'fb_receipt-200',
      status: 'stored',
      received_at: '2026-08-17T10:00:00Z',
    }), { status: 200 })));

    await expect(submitFeedback(input)).resolves.toMatchObject({
      receiptId: 'fb_receipt-200',
      status: 'stored',
    });
  });

  it('rejects a queued 202 response and preserves Retry-After', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      receipt_id: 'fb_queued',
      status: 'queued',
      received_at: '2026-08-17T10:00:00Z',
    }), {
      status: 202,
      headers: { 'Retry-After': '17' },
    })));

    await expect(submitFeedback(input)).rejects.toMatchObject({
      name: 'FeedbackApiError',
      message: 'feedback_submit_failed',
      status: 202,
      retryAfter: 17,
    });
  });

  it('leaves Retry-After null when an HTTP failure has no retry hint', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 503 })));

    await expect(submitFeedback(input)).rejects.toMatchObject({
      name: 'FeedbackApiError',
      message: 'feedback_submit_failed',
      status: 503,
      retryAfter: null,
    });
  });

  it.each([
    { header: ' ', retryAfter: null },
    { header: '-1', retryAfter: null },
    { header: '1.5', retryAfter: null },
    { header: '17', retryAfter: 17 },
  ])('accepts only a nonnegative integer Retry-After delta: $header', async ({ header, retryAfter }) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, {
      status: 503,
      headers: { 'Retry-After': header },
    })));

    await expect(submitFeedback(input)).rejects.toMatchObject({
      name: 'FeedbackApiError',
      status: 503,
      retryAfter,
    });
  });

  it.each([
    { receipt_id: 42, status: 'stored', received_at: '2026-08-17T10:00:00Z' },
    { receipt_id: 'fb_receipt', status: 'queued', received_at: '2026-08-17T10:00:00Z' },
    { receipt_id: 'fb_receipt', status: 'stored', received_at: null },
    { receipt_id: '  ', status: 'stored', received_at: '2026-08-17T10:00:00Z' },
    { receipt_id: 'fb_receipt', status: 'stored', received_at: '' },
    { receipt_id: 'fb_receipt', status: 'stored', received_at: 'not-a-time' },
    { receipt_id: 'fb_receipt', status: 'stored', received_at: '2026-08-17T10:00:00' },
    { receipt_id: 'fb_receipt', status: 'stored', received_at: '2026-08-17T10:00:00+03:00' },
  ])('rejects a malformed success receipt', async (payload) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), { status: 201 })));

    await expect(submitFeedback(input)).rejects.toMatchObject({
      name: 'FeedbackApiError',
      message: 'invalid_feedback_receipt',
      status: 201,
    });
  });

  it('rethrows an AbortError raised while reading the submission body', async () => {
    const controller = new AbortController();
    const response = new Response('{}', { status: 201 });
    const abortError = new DOMException('submission body read aborted', 'AbortError');
    vi.spyOn(response, 'json').mockRejectedValue(abortError);
    const fetchMock = vi.fn().mockResolvedValue(response);
    vi.stubGlobal('fetch', fetchMock);

    await expect(submitFeedback({ ...input, signal: controller.signal })).rejects.toBe(abortError);
    expect(fetchMock).toHaveBeenCalledWith('/api/feedback', expect.objectContaining({
      signal: controller.signal,
    }));
  });

  it('rejects an invalid JSON success body as a typed API error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{not-json', { status: 201 })));

    await expect(submitFeedback(input)).rejects.toMatchObject({
      name: 'FeedbackApiError',
      message: 'invalid_feedback_receipt',
      status: 201,
    });
  });
});

describe('feedback idempotency keys', () => {
  it('prefixes a random UUID with feedback', () => {
    const randomUUID = vi.fn().mockReturnValue('uuid-123');
    vi.stubGlobal('crypto', { randomUUID });

    expect(createFeedbackIdempotencyKey()).toBe('feedback-uuid-123');
    expect(randomUUID).toHaveBeenCalledOnce();
  });
});

it('exposes a typed error class for callers', () => {
  const error = new FeedbackApiError('failure', 503, 12);

  expect(error).toBeInstanceOf(Error);
  expect(error).toBeInstanceOf(FeedbackApiError);
  expect(error.status).toBe(503);
  expect(error.retryAfter).toBe(12);
});
