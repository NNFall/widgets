import { afterEach, expect, it, vi } from 'vitest';

import {
  createBillingCheckout,
  createProjectRefinement,
  disableBillingAutoRenew,
  getBillingPayment,
  getPendingBillingPayment,
  getBillingSubscription,
  getProjectPublication,
  getProjectVersions,
  publishProject,
  rollbackPublication,
  restoreProjectVersion,
  resumeBillingPayment,
  sendPreviewChat,
} from './api';

it('lists versions and sends CSRF-protected refinement and restore requests', async () => {
  const versions = {
    active_version_id: 'version-2',
    versions: [{
      id: 'version-2',
      ordinal: 2,
      kind: 'refinement',
      change_request: 'Сделай приветствие короче',
      parent_version_id: 'version-1',
      run_id: 'run-2',
      artifact_id: 'artifact-2',
      active: true,
      created_at: '2026-07-30T08:00:00Z',
    }],
  };
  const run = { id: 'run-3', project_id: 'project/123', status: 'queued' };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify(versions), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify(run), {
      status: 202,
      headers: { 'Content-Type': 'application/json' },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ version: versions.versions[0] }), {
      status: 201,
      headers: { 'Content-Type': 'application/json' },
    }));
  vi.stubGlobal('fetch', fetchMock);

  await expect(getProjectVersions('project/123')).resolves.toEqual(versions);
  await expect(createProjectRefinement(
    'project/123',
    'Сделай приветствие короче',
    'csrf-version',
    'refine-key',
  )).resolves.toMatchObject({ id: 'run-3' });
  await expect(restoreProjectVersion(
    'project/123',
    'version/2',
    'csrf-version',
    'restore-key',
  )).resolves.toEqual({ version: versions.versions[0] });

  const [listUrl, listInit] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(listUrl).toBe('/api/projects/project%2F123/versions');
  expect(listInit.credentials).toBe('include');

  const [refineUrl, refineInit] = fetchMock.mock.calls[1] as [string, RequestInit];
  expect(refineUrl).toBe('/api/projects/project%2F123/refinements');
  expect(new Headers(refineInit.headers).get('X-CSRF-Token')).toBe('csrf-version');
  expect(new Headers(refineInit.headers).get('Idempotency-Key')).toBe('refine-key');
  expect(JSON.parse(String(refineInit.body))).toEqual({
    change_request: 'Сделай приветствие короче',
  });

  const [restoreUrl, restoreInit] = fetchMock.mock.calls[2] as [string, RequestInit];
  expect(restoreUrl).toBe('/api/projects/project%2F123/versions/version%2F2/restore');
  expect(new Headers(restoreInit.headers).get('X-CSRF-Token')).toBe('csrf-version');
  expect(new Headers(restoreInit.headers).get('Idempotency-Key')).toBe('restore-key');
});

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
    'project-123',
    true,
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
  expect(JSON.parse(String(init.body))).toEqual({
    plan_code: 'starter_monthly',
    project_id: 'project-123',
    auto_renew: true,
  });
});

it('disables auto-renew through an owner-scoped CSRF-protected route', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
    subscription: {
      id: 'subscription/123',
      plan_code: 'starter_monthly',
      status: 'active',
      current_period_start: '2026-07-28T12:00:00Z',
      current_period_end: '2026-08-28T12:00:00Z',
      auto_renew: false,
      next_renewal_at: null,
    },
  }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
  vi.stubGlobal('fetch', fetchMock);

  await expect(
    disableBillingAutoRenew('subscription/123', 'csrf-billing'),
  ).resolves.toMatchObject({
    subscription: { id: 'subscription/123', auto_renew: false },
  });

  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(url).toBe(
    '/api/billing/subscriptions/subscription%2F123/auto-renew/off',
  );
  expect(init.credentials).toBe('include');
  expect(init.method).toBe('POST');
  expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('csrf-billing');
  expect(init.body).toBeUndefined();
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

it('publishes the selected artifact with allowed domains and rolls back an owned publication', async () => {
  const published = {
    publication_id: 'publication/123',
    release_id: 'release/456',
    artifact_id: 'artifact/789',
    stable_key: 'stable-widget-key',
    revision: 4,
    allowed_domains: ['https://example.com', 'https://shop.example.com'],
    checksum: 'sha256',
    embed_url: 'https://widgets.kaigo.space/embed/stable-widget-key.js',
    runtime_url: 'https://widgets.kaigo.space/runtime/stable-widget-key',
  };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify(published), {
      status: 201,
      headers: { 'Content-Type': 'application/json' },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify({
      ...published,
      release_id: 'release/old',
      revision: 3,
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
  vi.stubGlobal('fetch', fetchMock);

  await expect(publishProject(
    'project/123',
    {
      artifact_id: 'artifact/789',
      revision: 4,
      allowed_domains: ['https://example.com', 'https://shop.example.com'],
    },
    'csrf-publication',
  )).resolves.toEqual(published);
  await expect(rollbackPublication(
    'publication/123',
    'release/old',
    'csrf-publication',
  )).resolves.toMatchObject({ release_id: 'release/old', revision: 3 });

  const [publishUrl, publishInit] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(publishUrl).toBe('/api/projects/project%2F123/publish');
  expect(publishInit.credentials).toBe('include');
  expect(publishInit.method).toBe('POST');
  expect(new Headers(publishInit.headers).get('X-CSRF-Token')).toBe('csrf-publication');
  expect(JSON.parse(String(publishInit.body))).toEqual({
    artifact_id: 'artifact/789',
    revision: 4,
    allowed_domains: ['https://example.com', 'https://shop.example.com'],
  });

  const [rollbackUrl, rollbackInit] = fetchMock.mock.calls[1] as [string, RequestInit];
  expect(rollbackUrl).toBe('/api/publications/publication%2F123/rollback');
  expect(rollbackInit.credentials).toBe('include');
  expect(rollbackInit.method).toBe('POST');
  expect(new Headers(rollbackInit.headers).get('X-CSRF-Token')).toBe('csrf-publication');
  expect(JSON.parse(String(rollbackInit.body))).toEqual({
    target_release_id: 'release/old',
  });
});

it('hydrates owner publication history through an authenticated read without CSRF', async () => {
  const publication = {
    publication_id: 'publication-123',
    stable_key: 'stable-widget',
    state: 'published',
    allowed_domains: ['https://example.com'],
    embed_url: 'https://widgets.kaigo.space/embed/stable-widget.js',
    runtime_url: 'https://widgets.kaigo.space/runtime/stable-widget',
    active_release: {
      release_id: 'release-2',
      artifact_id: 'artifact-2',
      previous_release_id: 'release-1',
      revision: 2,
      checksum: 'checksum-2',
      created_at: '2026-07-28T13:00:00Z',
    },
    releases: [],
  };
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ publication }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  }));
  vi.stubGlobal('fetch', fetchMock);

  await expect(getProjectPublication('project/123')).resolves.toEqual({ publication });

  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(url).toBe('/api/projects/project%2F123/publication');
  expect(init.credentials).toBe('include');
  expect(init.method).toBeUndefined();
  expect(new Headers(init.headers).has('X-CSRF-Token')).toBe(false);
});
