import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from './api';
import { UpgradeGate } from './UpgradeGate';

vi.mock('./api', async (importOriginal) => {
  const original = await importOriginal<typeof import('./api')>();
  return {
    ...original,
    createBillingCheckout: vi.fn(),
    disableBillingAutoRenew: vi.fn(),
    getBillingPayment: vi.fn(),
    getPendingBillingPayment: vi.fn(),
    getBillingSubscription: vi.fn(),
    getProjectPublication: vi.fn(),
    publishProject: vi.fn(),
    rollbackPublication: vi.fn(),
    resumeBillingPayment: vi.fn(),
  };
});

const payment = {
  id: 'payment-123',
  plan_code: 'starter_monthly',
  status: 'pending' as const,
  amount_minor: 199_000,
  currency: 'RUB',
  created_at: '2026-07-28T12:00:00Z',
};

const gateProps = {
  csrfToken: 'csrf-billing',
  projectId: 'project-123',
  artifactId: 'artifact-123',
  revision: 4,
};

const activeSubscription = {
  id: 'subscription-123',
  plan_code: 'starter_monthly',
  status: 'active' as const,
  current_period_start: '2026-07-28T12:00:00Z',
  current_period_end: '2026-08-28T12:00:00Z',
  auto_renew: true,
  next_renewal_at: '2026-08-28T12:00:00Z',
};

describe('UpgradeGate', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    vi.stubGlobal('crypto', { randomUUID: vi.fn(() => 'checkout-request-123') });
    vi.mocked(api.getBillingSubscription).mockResolvedValue({ subscription: null });
    vi.mocked(api.getPendingBillingPayment).mockResolvedValue({
      payment: null,
      checkout_url: null,
    });
    vi.mocked(api.getProjectPublication).mockResolvedValue({ publication: null });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('opens checkout safely and reuses one idempotency key for the attempt', async () => {
    const replace = vi.fn();
    const close = vi.fn();
    const paymentWindow = {
      location: { replace },
      close,
      closed: false,
      opener: window,
    } as unknown as Window;
    const open = vi.spyOn(window, 'open').mockReturnValue(paymentWindow);
    vi.mocked(api.createBillingCheckout).mockResolvedValue({
      payment,
      checkout_url: 'https://yoomoney.ru/checkout/payment-123',
      created: true,
    });

    render(<UpgradeGate csrfToken="csrf-billing" projectId="project-123" />);
    await act(async () => {
      await Promise.resolve();
    });
    fireEvent.click(screen.getByRole('button', { name: /опубликовать и подключить/i }));

    await act(async () => {
      await Promise.resolve();
    });
    expect(api.createBillingCheckout).toHaveBeenCalledWith(
      'starter_monthly',
      'csrf-billing',
      'checkout-request-123',
      'project-123',
      false,
    );
    expect(open).toHaveBeenCalledWith('about:blank', '_blank');
    expect(paymentWindow.opener).toBeNull();
    expect(replace).toHaveBeenCalledWith('https://yoomoney.ru/checkout/payment-123');
    expect(close).not.toHaveBeenCalled();
    expect(screen.getByRole('status')).toHaveTextContent(/ожидаем подтверждение оплаты/i);
  });

  it('sends auto-renew only after the user explicitly selects the consent checkbox', async () => {
    vi.spyOn(window, 'open').mockReturnValue(null);
    vi.mocked(api.createBillingCheckout).mockResolvedValue({
      payment,
      checkout_url: 'https://yoomoney.ru/checkout/payment-123',
      created: true,
    });

    render(<UpgradeGate csrfToken="csrf-billing" projectId="project-123" />);
    await act(async () => {
      await Promise.resolve();
    });

    const consent = screen.getByRole('checkbox', {
      name: /продлевать тариф автоматически/i,
    });
    expect(consent).not.toBeChecked();
    fireEvent.click(consent);
    fireEvent.click(screen.getByRole('button', { name: /опубликовать и подключить/i }));
    await act(async () => Promise.resolve());

    expect(api.createBillingCheckout).toHaveBeenCalledWith(
      'starter_monthly',
      'csrf-billing',
      'checkout-request-123',
      'project-123',
      true,
    );
  });

  it('freezes renewal consent with the idempotency key across an ambiguous retry', async () => {
    vi.spyOn(window, 'open').mockReturnValue(null);
    vi.mocked(api.createBillingCheckout)
      .mockRejectedValueOnce(new Error('network outcome unknown'))
      .mockResolvedValueOnce({
        payment,
        checkout_url: 'https://yoomoney.ru/checkout/payment-123',
        created: false,
      });

    render(<UpgradeGate csrfToken="csrf-billing" projectId="project-123" />);
    await act(async () => {
      await Promise.resolve();
    });
    const consent = screen.getByRole('checkbox', {
      name: /продлевать тариф автоматически/i,
    });
    fireEvent.click(screen.getByRole('button', { name: /опубликовать и подключить/i }));
    await act(async () => Promise.resolve());

    expect(consent).toBeDisabled();
    expect(consent).not.toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: /опубликовать и подключить/i }));
    await act(async () => Promise.resolve());

    expect(api.createBillingCheckout).toHaveBeenNthCalledWith(
      1,
      'starter_monthly',
      'csrf-billing',
      'checkout-request-123',
      'project-123',
      false,
    );
    expect(api.createBillingCheckout).toHaveBeenNthCalledWith(
      2,
      'starter_monthly',
      'csrf-billing',
      'checkout-request-123',
      'project-123',
      false,
    );
  });

  it('polls single-flight and stops after the subscription becomes active', async () => {
    vi.spyOn(window, 'open').mockReturnValue(null);
    vi.mocked(api.createBillingCheckout).mockResolvedValue({
      payment,
      checkout_url: 'https://yoomoney.ru/checkout/payment-123',
      created: true,
    });
    let resolvePayment: ((value: Awaited<ReturnType<typeof api.getBillingPayment>>) => void) | null = null;
    vi.mocked(api.getBillingPayment).mockImplementation(() => new Promise((resolve) => {
      resolvePayment = resolve;
    }));
    vi.mocked(api.getBillingSubscription)
      .mockResolvedValueOnce({ subscription: null })
      .mockResolvedValueOnce({
        subscription: activeSubscription,
      });

    render(
      <UpgradeGate
        csrfToken="csrf-billing"
        projectId="project-123"
        pollIntervalMs={100}
      />,
    );
    await act(async () => {
      await Promise.resolve();
    });
    fireEvent.click(screen.getByRole('button', { name: /опубликовать и подключить/i }));
    await act(async () => Promise.resolve());

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(api.getBillingPayment).toHaveBeenCalledOnce();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    expect(api.getBillingPayment).toHaveBeenCalledOnce();

    await act(async () => {
      resolvePayment?.({
        payment: { ...payment, status: 'succeeded' },
      });
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(api.getBillingSubscription).toHaveBeenCalledTimes(2);
    expect(screen.getByText(/тариф активирован/i)).toBeVisible();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(api.getBillingPayment).toHaveBeenCalledOnce();
  });

  it('polls only the local payment endpoint every 3 seconds for at most 400 attempts', async () => {
    vi.mocked(api.getPendingBillingPayment).mockResolvedValue({
      payment,
      checkout_url: 'https://checkout.yookassa.ru/payment-123',
    });
    vi.mocked(api.getBillingPayment).mockResolvedValue({ payment });

    render(<UpgradeGate csrfToken="csrf-billing" />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_999);
    });
    expect(api.getBillingPayment).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(api.getBillingPayment).toHaveBeenCalledOnce();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(399 * 3_000);
    });
    expect(api.getBillingPayment).toHaveBeenCalledTimes(400);
    expect(api.resumeBillingPayment).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent(
      /подтверждение оплаты заняло больше обычного/i,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(api.getBillingPayment).toHaveBeenCalledTimes(400);
  });

  it('aborts polling and never updates after unmount', async () => {
    vi.spyOn(window, 'open').mockReturnValue(null);
    vi.mocked(api.createBillingCheckout).mockResolvedValue({
      payment,
      checkout_url: 'https://yoomoney.ru/checkout/payment-123',
      created: true,
    });
    let capturedSignal: AbortSignal | undefined;
    vi.mocked(api.getBillingPayment).mockImplementation((_paymentId, signal) => {
      capturedSignal = signal;
      return new Promise(() => undefined);
    });

    const view = render(<UpgradeGate csrfToken="csrf-billing" pollIntervalMs={100} />);
    await act(async () => {
      await Promise.resolve();
    });
    fireEvent.click(screen.getByRole('button', { name: /опубликовать и подключить/i }));
    await act(async () => Promise.resolve());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(capturedSignal?.aborted).toBe(false);

    view.unmount();

    expect(capturedSignal?.aborted).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(api.getBillingPayment).toHaveBeenCalledOnce();
  });

  it('starts a new idempotent attempt after a terminal cancellation', async () => {
    vi.mocked(crypto.randomUUID)
      .mockReturnValueOnce('00000000-0000-4000-8000-000000000001')
      .mockReturnValueOnce('00000000-0000-4000-8000-000000000002');
    vi.spyOn(window, 'open').mockReturnValue(null);
    vi.mocked(api.createBillingCheckout).mockResolvedValue({
      payment,
      checkout_url: 'https://yoomoney.ru/checkout/payment-123',
      created: true,
    });
    vi.mocked(api.getBillingPayment).mockResolvedValue({
      payment: { ...payment, status: 'cancelled' },
    });

    render(
      <UpgradeGate
        csrfToken="csrf-billing"
        projectId="project-123"
        pollIntervalMs={100}
      />,
    );
    await act(async () => {
      await Promise.resolve();
    });
    fireEvent.click(screen.getByRole('button', { name: /опубликовать и подключить/i }));
    await act(async () => Promise.resolve());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(screen.getByRole('alert')).toHaveTextContent(/оплата не завершена/i);

    fireEvent.click(screen.getByRole('button', { name: /опубликовать и подключить/i }));
    await act(async () => Promise.resolve());

    expect(api.createBillingCheckout).toHaveBeenNthCalledWith(
      1,
      'starter_monthly',
      'csrf-billing',
      '00000000-0000-4000-8000-000000000001',
      'project-123',
      false,
    );
    expect(api.createBillingCheckout).toHaveBeenNthCalledWith(
      2,
      'starter_monthly',
      'csrf-billing',
      '00000000-0000-4000-8000-000000000002',
      'project-123',
      false,
    );
  });

  it('restores an already active subscription without offering another checkout', async () => {
    vi.mocked(api.getBillingSubscription).mockResolvedValue({
      subscription: activeSubscription,
    });

    render(<UpgradeGate csrfToken="csrf-billing" />);
    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByText('Тариф активирован')).toBeVisible();
    expect(screen.queryByRole('button', { name: /опубликовать и подключить/i })).not.toBeInTheDocument();
    expect(api.createBillingCheckout).not.toHaveBeenCalled();
  });

  it('disables auto-renew without hiding the active entitlement', async () => {
    vi.mocked(api.getBillingSubscription).mockResolvedValue({
      subscription: activeSubscription,
    });
    vi.mocked(api.disableBillingAutoRenew).mockResolvedValue({
      subscription: {
        ...activeSubscription,
        auto_renew: false,
        next_renewal_at: null,
      },
    });

    render(<UpgradeGate csrfToken="csrf-billing" />);
    await act(async () => {
      await Promise.resolve();
    });

    fireEvent.click(screen.getByRole('button', { name: /отключить автопродление/i }));
    await act(async () => {
      await Promise.resolve();
    });

    expect(api.disableBillingAutoRenew).toHaveBeenCalledWith(
      'subscription-123',
      'csrf-billing',
    );
    expect(screen.getByText(/автопродление выключено/i)).toBeVisible();
    expect(screen.getByText(/доступ сохранится до/i)).toBeVisible();
    expect(screen.getByText('Тариф активирован')).toBeVisible();
  });

  it('publishes the selected accepted artifact, shows a stable embed snippet, and rolls back a known prior release', async () => {
    vi.mocked(api.getBillingSubscription).mockResolvedValue({
      subscription: activeSubscription,
    });
    vi.mocked(api.publishProject)
      .mockResolvedValueOnce({
        publication_id: 'publication-123',
        release_id: 'release-4',
        artifact_id: 'artifact-123',
        stable_key: 'stable-widget',
        revision: 4,
        allowed_domains: ['https://example.com'],
        checksum: 'checksum-4',
        embed_url: 'https://widgets.kaigo.space/embed/stable-widget.js',
        runtime_url: 'https://widgets.kaigo.space/runtime/stable-widget',
      })
      .mockResolvedValueOnce({
        publication_id: 'publication-123',
        release_id: 'release-5',
        artifact_id: 'artifact-456',
        stable_key: 'stable-widget',
        revision: 5,
        allowed_domains: ['https://example.com', 'https://shop.example.com'],
        checksum: 'checksum-5',
        embed_url: 'https://widgets.kaigo.space/embed/stable-widget.js',
        runtime_url: 'https://widgets.kaigo.space/runtime/stable-widget',
      });
    vi.mocked(api.rollbackPublication).mockResolvedValue({
      publication_id: 'publication-123',
      release_id: 'release-4',
      artifact_id: 'artifact-123',
      stable_key: 'stable-widget',
      revision: 4,
      allowed_domains: ['https://example.com', 'https://shop.example.com'],
      checksum: 'checksum-4',
      embed_url: 'https://widgets.kaigo.space/embed/stable-widget.js',
      runtime_url: 'https://widgets.kaigo.space/runtime/stable-widget',
    });

    const view = render(<UpgradeGate {...gateProps} />);
    await act(async () => {
      await Promise.resolve();
    });
    fireEvent.change(screen.getByLabelText('Разрешённые домены'), {
      target: { value: 'https://example.com' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Опубликовать виджет' }));
    await act(async () => {
      await Promise.resolve();
    });

    expect(api.publishProject).toHaveBeenNthCalledWith(
      1,
      'project-123',
      {
        artifact_id: 'artifact-123',
        revision: 4,
        allowed_domains: ['https://example.com'],
      },
      'csrf-billing',
    );
    expect(screen.getByText(
      '<script src="https://widgets.kaigo.space/embed/stable-widget.js" async></script>',
    )).toBeVisible();
    expect(screen.getByText(
      'Чат в предпросмотре нужен для проверки ответов посетителю. Он не изменяет сам виджет.',
    )).toBeVisible();
    expect(screen.queryByText(/дорабат/i)).not.toBeInTheDocument();

    view.rerender(
      <UpgradeGate
        {...gateProps}
        artifactId="artifact-456"
        revision={5}
      />,
    );
    fireEvent.change(screen.getByLabelText('Разрешённые домены'), {
      target: { value: 'https://example.com\nhttps://shop.example.com' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Обновить публикацию' }));
    await act(async () => {
      await Promise.resolve();
    });
    expect(api.publishProject).toHaveBeenNthCalledWith(
      2,
      'project-123',
      {
        artifact_id: 'artifact-456',
        revision: 5,
        allowed_domains: ['https://example.com', 'https://shop.example.com'],
      },
      'csrf-billing',
    );

    fireEvent.click(screen.getByRole('button', { name: 'Откатить к ревизии 4' }));
    await act(async () => {
      await Promise.resolve();
    });
    expect(api.rollbackPublication).toHaveBeenCalledWith(
      'publication-123',
      'release-4',
      'csrf-billing',
    );
  });

  it('hydrates stable embed and rollback targets after Studio reload', async () => {
    vi.useRealTimers();
    vi.mocked(api.getBillingSubscription).mockResolvedValue({
      subscription: activeSubscription,
    });
    vi.mocked(api.getProjectPublication).mockResolvedValue({
      publication: {
        publication_id: 'publication-123',
        stable_key: 'stable-widget',
        state: 'published',
        allowed_domains: ['https://example.com', 'https://shop.example.com'],
        embed_url: 'https://widgets.kaigo.space/embed/stable-widget.js',
        runtime_url: 'https://widgets.kaigo.space/runtime/stable-widget',
        active_release: {
          release_id: 'release-5',
          artifact_id: 'artifact-456',
          previous_release_id: 'release-4',
          revision: 5,
          checksum: 'checksum-5',
          created_at: '2026-07-28T13:00:00Z',
        },
        releases: [
          {
            release_id: 'release-4',
            artifact_id: 'artifact-123',
            previous_release_id: null,
            revision: 4,
            checksum: 'checksum-4',
            created_at: '2026-07-28T12:00:00Z',
          },
          {
            release_id: 'release-5',
            artifact_id: 'artifact-456',
            previous_release_id: 'release-4',
            revision: 5,
            checksum: 'checksum-5',
            created_at: '2026-07-28T13:00:00Z',
          },
        ],
      },
    });
    vi.mocked(api.rollbackPublication).mockResolvedValue({
      publication_id: 'publication-123',
      release_id: 'release-4',
      artifact_id: 'artifact-123',
      stable_key: 'stable-widget',
      revision: 4,
      allowed_domains: ['https://example.com', 'https://shop.example.com'],
      checksum: 'checksum-4',
      embed_url: 'https://widgets.kaigo.space/embed/stable-widget.js',
      runtime_url: 'https://widgets.kaigo.space/runtime/stable-widget',
    });

    render(<UpgradeGate {...gateProps} />);
    expect(await screen.findByText(
      '<script src="https://widgets.kaigo.space/embed/stable-widget.js" async></script>',
    )).toBeVisible();
    expect(screen.getByLabelText('Разрешённые домены')).toHaveValue(
      'https://example.com\nhttps://shop.example.com',
    );

    fireEvent.click(screen.getByRole('button', { name: 'Откатить к ревизии 4' }));
    await act(async () => {
      await Promise.resolve();
    });
    expect(api.rollbackPublication).toHaveBeenCalledWith(
      'publication-123',
      'release-4',
      'csrf-billing',
    );
  });

  it('fails closed when initial billing recovery fails and retries the check explicitly', async () => {
    vi.mocked(api.getBillingSubscription)
      .mockRejectedValueOnce(new api.BuilderApiError('Unavailable', {
        status: 503,
        code: 'billing_unavailable',
        raw: 'Unavailable',
        retryable: true,
      }))
      .mockResolvedValueOnce({ subscription: null });

    render(<UpgradeGate csrfToken="csrf-billing" />);
    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.queryByRole('button', { name: /опубликовать и подключить/i })).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(/не удалось проверить тариф/i);
    fireEvent.click(screen.getByRole('button', { name: /повторить проверку/i }));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(api.getBillingSubscription).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('button', { name: /опубликовать и подключить/i })).toBeEnabled();
    expect(api.createBillingCheckout).not.toHaveBeenCalled();
  });

  it('restores a pending checkout after reload and resumes polling without creating another one', async () => {
    vi.mocked(api.getPendingBillingPayment).mockResolvedValue({
      payment: { ...payment, status: 'creating' },
      checkout_url: 'https://checkout.yookassa.ru/payment-123',
    });
    vi.mocked(api.getBillingPayment).mockImplementation(() => new Promise(() => undefined));

    render(<UpgradeGate csrfToken="csrf-billing" pollIntervalMs={100} />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByRole('link', { name: /перейти к оплате/i })).toHaveAttribute(
      'href',
      'https://checkout.yookassa.ru/payment-123',
    );
    expect(api.createBillingCheckout).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(api.getBillingPayment).toHaveBeenCalledWith(
      'payment-123',
      expect.any(AbortSignal),
    );
  });

  it('fails closed when pending payment recovery fails', async () => {
    vi.mocked(api.getPendingBillingPayment).mockRejectedValue(new Error('network down'));

    render(<UpgradeGate csrfToken="csrf-billing" />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByRole('alert')).toHaveTextContent(/не удалось проверить тариф/i);
    expect(screen.queryByRole('button', { name: /опубликовать и подключить/i })).not.toBeInTheDocument();
    expect(api.createBillingCheckout).not.toHaveBeenCalled();
  });

  it('rejects an HTTPS checkout URL outside YooKassa and YooMoney', async () => {
    const replace = vi.fn();
    const close = vi.fn();
    vi.spyOn(window, 'open').mockReturnValue({
      location: { replace },
      close,
      closed: false,
      opener: window,
    } as unknown as Window);
    vi.mocked(api.createBillingCheckout).mockResolvedValue({
      payment,
      checkout_url: 'https://evil.example/checkout/payment-123',
      created: true,
    });

    render(<UpgradeGate csrfToken="csrf-billing" />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    fireEvent.click(screen.getByRole('button', { name: /опубликовать и подключить/i }));
    await act(async () => {
      await Promise.resolve();
    });

    expect(replace).not.toHaveBeenCalled();
    expect(close).toHaveBeenCalledOnce();
    expect(screen.getByRole('alert')).toHaveTextContent(/не удалось открыть оплату/i);
  });

  it.each(['creating', 'failed'] as const)(
    'reconciles a stored %s attempt after a crash without creating a new checkout',
    async (recoverableStatus) => {
    vi.mocked(api.getPendingBillingPayment).mockResolvedValue({
      payment: { ...payment, status: recoverableStatus },
      checkout_url: null,
    });
    vi.mocked(api.resumeBillingPayment).mockResolvedValue({
      payment: { ...payment, status: 'pending' },
      checkout_url: 'https://checkout.yookassa.ru/payment-123',
      created: false,
    });
    vi.mocked(api.getBillingPayment).mockImplementation(() => new Promise(() => undefined));

    render(<UpgradeGate csrfToken="csrf-billing" pollIntervalMs={100} />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(api.resumeBillingPayment).toHaveBeenCalledWith('payment-123', 'csrf-billing');
    expect(api.createBillingCheckout).not.toHaveBeenCalled();
    expect(screen.getByRole('link', { name: /перейти к оплате/i })).toHaveAttribute(
      'href',
      'https://checkout.yookassa.ru/payment-123',
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(api.getBillingPayment).toHaveBeenCalledOnce();
    },
  );

  it('fails closed with Retry when resuming a creating attempt fails', async () => {
    vi.mocked(api.getPendingBillingPayment).mockResolvedValue({
      payment: { ...payment, status: 'creating' },
      checkout_url: null,
    });
    vi.mocked(api.resumeBillingPayment).mockRejectedValue(new Error('resume unavailable'));

    render(<UpgradeGate csrfToken="csrf-billing" />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByRole('alert')).toHaveTextContent(/не удалось проверить тариф/i);
    expect(screen.getByRole('button', { name: /повторить проверку/i })).toBeEnabled();
    expect(api.createBillingCheckout).not.toHaveBeenCalled();
  });
});
