import { afterEach, expect, it, vi } from 'vitest';

import { sendPreviewChat } from './api';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it('preserves an explicit retryable flag from a Builder API error', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
    error: {
      code: 'chat_revision_mismatch',
      message: 'Revision is no longer active',
      retryable: false,
    },
  }), {
    status: 409,
    headers: { 'Content-Type': 'application/json' },
  })));

  await expect(sendPreviewChat('run-123', {
    request_id: 'request-error-123',
    message: 'Повторить вопрос?',
    revision: 4,
  })).rejects.toMatchObject({
    status: 409,
    code: 'chat_revision_mismatch',
    raw: 'Revision is no longer active',
    retryable: false,
  });
});

it.each([
  { retryable: true, expected: true, label: 'true' },
  { retryable: undefined, expected: false, label: 'absent' },
])('uses retryable=$label without inventing a retry hint', async ({ retryable, expected }) => {
  const error = {
    code: 'provider_unavailable',
    message: 'Provider unavailable',
    ...(retryable === undefined ? {} : { retryable }),
  };
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ error }), {
    status: 503,
    headers: { 'Content-Type': 'application/json' },
  })));

  await expect(sendPreviewChat('run-123', {
    request_id: 'request-error-123',
    message: 'Повторить вопрос?',
    revision: 4,
  })).rejects.toMatchObject({ retryable: expected });
});
