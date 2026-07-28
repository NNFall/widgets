import { afterEach, expect, it, vi } from 'vitest';

import {
  createBillingCheckout,
  getBillingPayment,
  getPendingBillingPayment,
  getBillingSubscription,
  resumeBillingPayment,
  sendPreviewChat,
} from './api';

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

it('creates a billing checkout with session credentials, CSRF and idempotency', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
    payment: {
      id: 'payment-123',
      plan_code: 'starter_monthly',
      status: 'pending',
      amount_minor: 199_000,
      currency: 'RUB',
      created_at: '2026-07-28T12:00:00Z',
    },
    checkout_url: 'https://yoomoney.ru/checkout/payment-123',
    created: true,
  }), {
    status: 201,
    headers: { 'Content-Type': 'application/json' },
  }));
  vi.stubGlobal('fetch', fetchMock);

  await expect(createBillingCheckout(
    'starter_monthly',
    'csrf-billing',
    'checkout-stable-key',
  )).resolves.toMatchObject({
    payment: { id: 'payment-123', status: 'pending' },
    created: true,
  });

  expect(fetchMock).toHaveBeenCalledOnce();
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(url).toBe('/api/billing/checkout');
  expect(init.credentials).toBe('include');
  expect(init.method).toBe('POST');
  expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('csrf-billing');
  expect(new Headers(init.headers).get('Idempotency-Key')).toBe('checkout-stable-key');
  expect(JSON.parse(String(init.body))).toEqual({ plan_code: 'starter_monthly' });
});

it('reads payment and subscription state through authenticated SaaS routes', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({
      payment: {
        id: 'payment/123',
        plan_code: 'starter_monthly',
        status: 'succeeded',
        amount_minor: 199_000,
        currency: 'RUB',
        created_at: '2026-07-28T12:00:00Z',
      },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    .mockResolvedValueOnce(new Response(JSON.stringify({
      subscription: {
        id: 'subscription-123',
        plan_code: 'starter_monthly',
        status: 'active',
        current_period_start: '2026-07-28T12:00:00Z',
        current_period_end: '2026-08-28T12:00:00Z',
      },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
  vi.stubGlobal('fetch', fetchMock);

  await expect(getBillingPayment('payment/123')).resolves.toMatchObject({
    payment: { status: 'succeeded' },
  });
  await expect(getBillingSubscription()).resolves.toMatchObject({
    subscription: { status: 'active' },
  });

  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
    '/api/billing/payments/payment%2F123',
    '/api/billing/subscription',
  ]);
  expect(fetchMock.mock.calls.every(([, init]) => init.credentials === 'include')).toBe(true);
});

it('restores the pending checkout through the authenticated pending-payment route', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
    payment: {
      id: 'payment-123',
      plan_code: 'starter_monthly',
      status: 'creating',
      amount_minor: 199_000,
      currency: 'RUB',
      created_at: '2026-07-28T12:00:00Z',
    },
    checkout_url: 'https://yookassa.ru/checkout/payment-123',
  }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
  vi.stubGlobal('fetch', fetchMock);

  await expect(getPendingBillingPayment()).resolves.toMatchObject({
    payment: { id: 'payment-123', status: 'creating' },
    checkout_url: 'https://yookassa.ru/checkout/payment-123',
  });

  expect(fetchMock).toHaveBeenCalledWith(
    '/api/billing/payments/pending',
    expect.objectContaining({ credentials: 'include' }),
  );
});

it('resumes the exact stored payment attempt with CSRF and no replacement payload', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
    payment: {
      id: 'payment/123',
      plan_code: 'starter_monthly',
      status: 'pending',
      amount_minor: 199_000,
      currency: 'RUB',
      created_at: '2026-07-28T12:00:00Z',
    },
    checkout_url: 'https://yookassa.ru/checkout/payment-123',
    created: false,
  }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
  vi.stubGlobal('fetch', fetchMock);

  await expect(resumeBillingPayment('payment/123', 'csrf-billing')).resolves.toMatchObject({
    payment: { id: 'payment/123', status: 'pending' },
    created: false,
  });

  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(url).toBe('/api/billing/payments/payment%2F123/resume');
  expect(init.credentials).toBe('include');
  expect(init.method).toBe('POST');
  expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('csrf-billing');
  expect(init.body).toBeUndefined();
});
