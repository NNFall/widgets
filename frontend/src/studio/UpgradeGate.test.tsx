import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from './api';
import { UpgradeGate } from './UpgradeGate';

vi.mock('./api', async (importOriginal) => {
  const original = await importOriginal<typeof import('./api')>();
  return {
    ...original,
    claimFounderAccess: vi.fn(),
    createBillingCheckout: vi.fn(),
    createCustomerContact: vi.fn(),
    disableBillingAutoRenew: vi.fn(),
    getBillingOffer: vi.fn(),
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
  amount_minor: 200_000,
  currency: 'RUB',
  created_at: '2026-07-28T12:00:00Z',
};

const billingOffer = {
  founder: {
    eligible: true,
    reason: null,
    remaining: 20,
    capacity: 20,
    period_days: 14,
    generation_tokens: 1_500_000,
  },
  plans: [
    {
      code: 'starter_intro_15d',
      title: 'Kaigo Starter, первые 15 дней',
      amount_minor: 50_000,
      currency: 'RUB',
      period_days: 15,
      generation_tokens: 500_000,
      renewal: {
        plan_code: 'starter_intro_balance_15d',
        amount_minor: 150_000,
        currency: 'RUB',
        period_days: 15,
        following: {
          plan_code: 'starter_monthly',
          amount_minor: 200_000,
          currency: 'RUB',
          period_days: 30,
        },
      },
    },
    {
      code: 'starter_monthly',
      title: 'Kaigo Starter, 1 месяц',
      amount_minor: 200_000,
      currency: 'RUB',
      period_days: 30,
      generation_tokens: 1_000_000,
      renewal: null,
    },
    {
      code: 'starter_quarterly',
      title: 'Kaigo Starter, 3 месяца',
      amount_minor: 500_000,
      currency: 'RUB',
      period_days: 90,
      generation_tokens: 3_000_000,
      renewal: null,
    },
  ],
};

const gateProps = {
  csrfToken: 'csrf-billing',
  projectId: 'project-123',
  versionsEnabled: true,
  projectVersionId: 'version-4',
  projectVersionOrdinal: 4,
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
  generation_tokens_remaining: 750_000,
};

function restoredPublication(allowedDomains: string[]) {
  return {
    publication: {
      publication_id: 'publication-123',
      stable_key: 'stable-widget',
      state: 'published' as const,
      allowed_domains: allowedDomains,
      embed_url: 'https://widgets.kaigo.space/embed/stable-widget.js',
      runtime_url: 'https://widgets.kaigo.space/runtime/stable-widget',
      active_release: {
        release_id: 'release-4',
        artifact_id: 'artifact-123',
        project_version_id: 'version-4',
        previous_release_id: null,
        revision: 4,
        checksum: 'checksum-4',
        created_at: '2026-07-28T13:00:00Z',
      },
      releases: [],
    },
  };
}

function updatedPublication(allowedDomains: string[]) {
  return {
    publication_id: 'publication-123',
    release_id: 'release-5',
    artifact_id: 'artifact-123',
    project_version_id: 'version-4',
    stable_key: 'stable-widget',
    revision: 4,
    allowed_domains: allowedDomains,
    checksum: 'checksum-5',
    embed_url: 'https://widgets.kaigo.space/embed/stable-widget.js',
    runtime_url: 'https://widgets.kaigo.space/runtime/stable-widget',
  };
}

function expectManualCopyValueVisible(value: string) {
  const visibleValue = screen.getAllByText(value, { exact: true }).find(
    (candidate) => !candidate.closest('details:not([open])'),
  );
  if (!visibleValue) {
    throw new Error(`Manual-copy value is still hidden: ${value}`);
  }
  expect(visibleValue).toBeVisible();
}

async function chooseMonthlyPlan() {
  await openPublicationOffer();
  const monthlyPlan = screen.getByRole('button', { name: /выбрать месяц/i });
  fireEvent.click(monthlyPlan);
  await act(async () => Promise.resolve());
}

async function openPublicationOffer() {
  const legacyAction = screen.queryByRole('button', {
    name: /выбрать условия публикации/i,
  });
  if (legacyAction) {
    fireEvent.click(legacyAction);
  }
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  expect(screen.getByRole('button', { name: /выбрать месяц/i })).toBeEnabled();
}

describe('UpgradeGate', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    vi.stubGlobal('crypto', { randomUUID: vi.fn(() => 'checkout-request-123') });
    vi.mocked(api.getBillingSubscription).mockResolvedValue({ subscription: null });
    vi.mocked(api.getBillingOffer).mockResolvedValue(billingOffer);
    vi.mocked(api.getPendingBillingPayment).mockResolvedValue({
      payment: null,
      checkout_url: null,
    });
    vi.mocked(api.getProjectPublication).mockResolvedValue({ publication: null });
    vi.mocked(api.publishProject).mockResolvedValue(updatedPublication(['https://example.com']));
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('shows the eligible Founder offer inline as soon as publication opens', async () => {
    vi.useRealTimers();

    render(<UpgradeGate {...gateProps} />);

    expect(await screen.findByRole('heading', {
      name: '14 дней полностью бесплатно',
    })).toBeVisible();
    expect(screen.getByText(/честную обратную связь/i)).toBeVisible();
    expect(screen.getByText(/без карты и автосписаний/i)).toBeVisible();
    expect(screen.getByRole('button', {
      name: 'Активировать бесплатно и продолжить',
    })).toBeEnabled();
    expect(screen.queryByRole('button', {
      name: /выбрать условия публикации/i,
    })).not.toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(document.querySelector('.publication-offer__backdrop')).not.toBeInTheDocument();
    expect(api.getBillingOffer).toHaveBeenCalledWith('project-123');
  });

  it('keeps initial billing recovery visible instead of claiming that payment is pending', async () => {
    vi.useRealTimers();
    type SubscriptionRecovery = Awaited<ReturnType<typeof api.getBillingSubscription>>;
    let resolveSubscription!: (value: SubscriptionRecovery) => void;
    vi.mocked(api.getBillingSubscription).mockImplementation(() => new Promise<SubscriptionRecovery>(
      (resolve) => {
        resolveSubscription = resolve;
      },
    ));

    render(<UpgradeGate {...gateProps} />);

    expect(screen.getByRole('heading', { name: 'Проверяем доступ' })).toBeVisible();
    expect(screen.queryByText('Ожидаем подтверждение оплаты…')).not.toBeInTheDocument();
    expect(api.getBillingOffer).not.toHaveBeenCalled();

    await act(async () => {
      resolveSubscription({ subscription: null });
      await Promise.resolve();
    });

    expect(await screen.findByRole('heading', {
      name: '14 дней полностью бесплатно',
    })).toBeVisible();
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
    await chooseMonthlyPlan();
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

  it('starts the 500-ruble offer without forcing renewal consent', async () => {
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

    await openPublicationOffer();
    const consent = screen.getByRole('checkbox', { name: /после оплаченных 15 дней/i });
    expect(consent).not.toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: /выбрать 15 дней/i }));
    await act(async () => Promise.resolve());

    expect(api.createBillingCheckout).toHaveBeenCalledWith(
      'starter_intro_15d',
      'csrf-billing',
      'checkout-request-123',
      'project-123',
      false,
    );
  });

  it('recovers an unavailable intro offer without consuming the retry key', async () => {
    const replace = vi.fn();
    const close = vi.fn();
    const paymentWindow = {
      location: { replace },
      close,
      closed: false,
      opener: window,
    } as unknown as Window;
    vi.spyOn(window, 'open').mockReturnValue(paymentWindow);
    vi.mocked(crypto.randomUUID)
      .mockReturnValueOnce('11111111-1111-4111-8111-111111111111')
      .mockReturnValueOnce('22222222-2222-4222-8222-222222222222');
    vi.mocked(api.createBillingCheckout)
      .mockRejectedValueOnce(new api.BuilderApiError('Intro offer unavailable', {
        status: 409,
        code: 'intro_offer_unavailable',
        raw: 'Intro offer unavailable',
      }))
      .mockResolvedValueOnce({
        payment,
        checkout_url: 'https://yoomoney.ru/checkout/payment-123',
        created: true,
      });

    render(<UpgradeGate csrfToken="csrf-billing" projectId="project-123" />);
    await act(async () => {
      await Promise.resolve();
    });
    await openPublicationOffer();

    fireEvent.click(screen.getByRole('button', { name: /выбрать 15 дней/i }));
    await act(async () => Promise.resolve());

    expect(close).toHaveBeenCalledOnce();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Вводный тариф уже использован. Выберите обычный тариф.',
    );
    expect(screen.queryByRole('button', { name: /выбрать 15 дней/i })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /выбрать месяц/i })).toBeVisible();
    expect(screen.queryByRole('button', { name: /выбрать условия публикации/i })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /выбрать месяц/i }));
    await act(async () => Promise.resolve());

    expect(api.createBillingCheckout).toHaveBeenNthCalledWith(
      1,
      'starter_intro_15d',
      'csrf-billing',
      '11111111-1111-4111-8111-111111111111',
      'project-123',
      false,
    );
    expect(api.createBillingCheckout).toHaveBeenNthCalledWith(
      2,
      'starter_monthly',
      'csrf-billing',
      '22222222-2222-4222-8222-222222222222',
      'project-123',
      false,
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
    await chooseMonthlyPlan();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /выбрать месяц/i })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: /выбрать месяц/i }));
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
    vi.useRealTimers();
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
    type PublicationRecovery = Awaited<ReturnType<typeof api.getProjectPublication>>;
    let resolvePublicationRecovery!: (value: PublicationRecovery) => void;
    vi.mocked(api.getProjectPublication).mockImplementation(() => new Promise<PublicationRecovery>(
      (resolve) => {
        resolvePublicationRecovery = resolve;
      },
    ));

    render(
      <UpgradeGate
        {...gateProps}
        pollIntervalMs={100}
      />,
    );
    await act(async () => {
      await Promise.resolve();
    });
    await chooseMonthlyPlan();

    await waitFor(() => expect(api.getBillingPayment).toHaveBeenCalledOnce());

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 250));
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
    expect(await screen.findByRole('heading', {
      name: 'Восстанавливаем публикацию',
    })).toBeVisible();
    expect(api.publishProject).not.toHaveBeenCalled();

    await act(async () => {
      resolvePublicationRecovery({ publication: null });
      await Promise.resolve();
    });

    await waitFor(() => expect(api.publishProject).toHaveBeenCalledTimes(1));
    expect(api.publishProject).toHaveBeenCalledTimes(1);
    expect(api.publishProject).toHaveBeenCalledWith(
      'project-123',
      {
        project_version_id: 'version-4',
        expected_active_release_id: null,
      },
      'csrf-billing',
    );
    expect(screen.getByRole('heading', { name: 'Виджет опубликован' })).toBeVisible();
    expect(screen.queryByText(/всё готово к публикации/i)).not.toBeInTheDocument();
    expect(screen.getByText('Доработки доступны в рамках тарифа.')).toBeVisible();
    expect(screen.queryByText(/750[\s ]000|токен/i)).not.toBeInTheDocument();

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 250));
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

    const view = render(<UpgradeGate csrfToken="csrf-billing" projectId="project-123" pollIntervalMs={100} />);
    await act(async () => {
      await Promise.resolve();
    });
    await chooseMonthlyPlan();
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
    await chooseMonthlyPlan();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(screen.getByRole('alert')).toHaveTextContent(/оплата не завершена/i);

    await chooseMonthlyPlan();

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

    expect(screen.queryByText('Всё готово к публикации')).not.toBeInTheDocument();
    expect(screen.getByText(/тариф действует до|следующее продление/i)).toBeVisible();
    expect(screen.queryByRole('button', { name: /выбрать условия публикации/i })).not.toBeInTheDocument();
    expect(api.createBillingCheckout).not.toHaveBeenCalled();
  });

  it('claims Founder access and immediately publishes the selected version without a card', async () => {
    vi.useRealTimers();
    const founderSubscription = {
      ...activeSubscription,
      id: 'founder-subscription-1',
      plan_code: 'founder_14d',
      plan_title: 'Kaigo Founder, 14 дней',
      access_kind: 'founder' as const,
      auto_renew: false,
      next_renewal_at: null,
      next_charge: null,
      current_period_end: '2026-08-27T12:00:00Z',
      generation_tokens_remaining: 1_500_000,
    };
    const founderClaim: Awaited<ReturnType<typeof api.claimFounderAccess>> = {
      created: true,
      founder: { position: 1, ends_at: '2026-08-27T12:00:00Z' },
      subscription: founderSubscription,
    };
    let resolveFounderClaim!: (value: typeof founderClaim) => void;
    vi.mocked(api.claimFounderAccess).mockImplementation(() => new Promise<typeof founderClaim>((resolve) => {
      resolveFounderClaim = resolve;
    }));
    vi.mocked(api.publishProject).mockResolvedValue({
      publication_id: 'publication-founder',
      release_id: 'release-founder',
      artifact_id: 'artifact-123',
      project_version_id: 'version-4',
      stable_key: 'founder-widget',
      revision: 4,
      allowed_domains: ['https://example.com'],
      checksum: 'founder-checksum',
      embed_url: 'https://widgets.kaigo.space/embed/founder-widget.js',
      runtime_url: 'https://widgets.kaigo.space/runtime/founder-widget',
    });

    render(<UpgradeGate {...gateProps} />);
    const founderAction = await screen.findByRole('button', {
      name: 'Активировать бесплатно и продолжить',
    });
    fireEvent.click(founderAction);

    await waitFor(() => expect(api.claimFounderAccess).toHaveBeenCalledWith(
      'project-123',
      'csrf-billing',
    ));
    expect(api.publishProject).not.toHaveBeenCalled();

    await act(async () => {
      resolveFounderClaim(founderClaim);
      await Promise.resolve();
    });
    await waitFor(() => expect(api.publishProject).toHaveBeenCalledWith(
      'project-123',
      {
        project_version_id: 'version-4',
        expected_active_release_id: null,
      },
      'csrf-billing',
    ));
    expect(screen.getByRole('heading', { name: 'Виджет опубликован' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Скопировать код установки' })).toBeVisible();
    expect(screen.queryByText('Всё готово к публикации')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {
      name: 'Опубликовать и получить код',
    })).not.toBeInTheDocument();
    expect(screen.getByText(/версия 4 опубликована и доступна/i)).toBeVisible();
    expect(screen.getByText(/автопродление выключено/i)).toBeVisible();
  });

  it('keeps access active after a rejected Founder publication and retries only publication', async () => {
    vi.useRealTimers();
    const founderSubscription = {
      ...activeSubscription,
      id: 'founder-subscription-1',
      plan_code: 'founder_14d',
      plan_title: 'Kaigo Founder, 14 дней',
      access_kind: 'founder' as const,
      auto_renew: false,
      next_renewal_at: null,
      next_charge: null,
      current_period_end: '2026-08-27T12:00:00Z',
      generation_tokens_remaining: 1_500_000,
    };
    vi.mocked(api.claimFounderAccess).mockResolvedValue({
      created: true,
      founder: { position: 1, ends_at: '2026-08-27T12:00:00Z' },
      subscription: founderSubscription,
    });
    vi.mocked(api.publishProject)
      .mockRejectedValueOnce(new api.BuilderApiError('invalid body', {
        status: 400,
        code: 'invalid_body',
        raw: 'invalid body',
      }))
      .mockResolvedValueOnce({
        publication_id: 'publication-founder',
        release_id: 'release-founder',
        artifact_id: 'artifact-123',
        project_version_id: 'version-4',
        stable_key: 'founder-widget',
        revision: 4,
        allowed_domains: ['https://example.com'],
        checksum: 'founder-checksum',
        embed_url: 'https://widgets.kaigo.space/embed/founder-widget.js',
        runtime_url: 'https://widgets.kaigo.space/runtime/founder-widget',
      });
    vi.mocked(api.createCustomerContact).mockResolvedValue({
      request_id: 'support-partial-publication',
      accepted: true,
    });

    render(<UpgradeGate {...gateProps} />);
    fireEvent.click(await screen.findByRole('button', {
      name: 'Активировать бесплатно и продолжить',
    }));

    expect(await screen.findByRole('heading', {
      name: 'Доступ подключён, публикация не завершена',
    })).toBeVisible();
    expect(api.claimFounderAccess).toHaveBeenCalledOnce();
    expect(api.publishProject).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('Всё готово к публикации')).not.toBeInTheDocument();
    expect(screen.queryByText(/invalid body/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('dialog', {
      name: 'Расскажите, как прошёл пилот',
    })).not.toBeInTheDocument();

    const supportAction = screen.getByRole('button', {
      name: 'Нужна помощь? Связаться с Kaigo',
    });
    supportAction.focus();
    fireEvent.click(supportAction);
    const supportDialog = screen.getByRole('dialog', { name: 'Связаться с Kaigo' });
    expect(supportDialog).toBeVisible();
    await waitFor(() => expect(supportDialog.contains(document.activeElement)).toBe(true));
    expect(document.getElementById('studio-publication')).toHaveAttribute('aria-hidden', 'true');
    expect(screen.queryByRole('dialog', {
      name: 'Расскажите, как прошёл пилот',
    })).not.toBeInTheDocument();
    fireEvent.keyDown(supportDialog, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Связаться с Kaigo' })).not.toBeInTheDocument();
    expect(supportAction).toHaveFocus();

    fireEvent.click(supportAction);
    const reopenedSupportDialog = screen.getByRole('dialog', { name: 'Связаться с Kaigo' });
    fireEvent.change(screen.getByRole('textbox', { name: 'Сообщение' }), {
      target: { value: 'Публикация не завершилась, помогите проверить запуск.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }));
    await waitFor(() => expect(api.createCustomerContact).toHaveBeenCalledWith(
      'project-123',
      'Публикация не завершилась, помогите проверить запуск.',
      'csrf-billing',
      'checkout-request-123',
      {
        kind: 'support',
        rating: undefined,
        testimonialAllowed: false,
      },
    ));
    expect(vi.mocked(api.createCustomerContact).mock.calls[0]?.[4]?.kind).not.toBe(
      'founder_feedback',
    );
    expect(await screen.findByText(
      'Сообщение сохранено. Мы свяжемся с вами по адресу аккаунта.',
    )).toBeVisible();
    await waitFor(() => expect(reopenedSupportDialog.contains(document.activeElement)).toBe(true));
    fireEvent.click(screen.getByRole('button', { name: 'Закрыть' }));

    fireEvent.click(screen.getByRole('button', { name: 'Повторить публикацию' }));

    expect(await screen.findByRole('heading', { name: 'Виджет опубликован' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Скопировать код установки' })).toBeVisible();
    expect(api.claimFounderAccess).toHaveBeenCalledOnce();
    expect(api.publishProject).toHaveBeenCalledTimes(2);
    expect(api.publishProject).toHaveBeenNthCalledWith(
      2,
      'project-123',
      {
        project_version_id: 'version-4',
        expected_active_release_id: null,
      },
      'csrf-billing',
    );
  });

  it('sends founder feedback with rating and testimonial consent from Studio', async () => {
    vi.useRealTimers();
    const founderSubscription = {
      ...activeSubscription,
      plan_code: 'founder_14d',
      access_kind: 'founder' as const,
      auto_renew: false,
      next_renewal_at: null,
    };
    vi.mocked(api.claimFounderAccess).mockResolvedValue({
      created: true,
      founder: { position: 1, ends_at: '2026-08-27T12:00:00Z' },
      subscription: founderSubscription,
    });
    vi.mocked(api.createCustomerContact).mockResolvedValue({
      request_id: 'contact-1',
      accepted: true,
    });
    let resolvePublication!: (
      value: Awaited<ReturnType<typeof api.publishProject>>,
    ) => void;
    vi.mocked(api.publishProject).mockImplementation(() => new Promise((resolve) => {
      resolvePublication = resolve;
    }));

    render(<UpgradeGate {...gateProps} />);
    fireEvent.click(await screen.findByRole('button', {
      name: 'Активировать бесплатно и продолжить',
    }));
    await waitFor(() => expect(api.publishProject).toHaveBeenCalledOnce());
    expect(screen.queryByRole('button', { name: 'Связаться с Kaigo' })).not.toBeInTheDocument();
    expect(screen.queryByRole('dialog', {
      name: 'Расскажите, как прошёл пилот',
    })).not.toBeInTheDocument();

    await act(async () => {
      resolvePublication(updatedPublication(['https://example.com']));
      await Promise.resolve();
    });
    await waitFor(() => expect(screen.getByRole('button', {
      name: 'Связаться с Kaigo',
    })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Связаться с Kaigo' }));
    const dialog = screen.getByRole('dialog', { name: 'Расскажите, как прошёл пилот' });
    fireEvent.change(screen.getByRole('textbox', { name: 'Сообщение' }), {
      target: { value: 'Виджет понравился, но нужна помощь с установкой.' },
    });
    fireEvent.click(screen.getByRole('radio', { name: '5' }));
    fireEvent.click(screen.getByRole('checkbox', { name: /можно использовать мой отзыв/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }));

    await waitFor(() => expect(api.createCustomerContact).toHaveBeenCalledWith(
      'project-123',
      'Виджет понравился, но нужна помощь с установкой.',
      'csrf-billing',
      'checkout-request-123',
      {
        kind: 'founder_feedback',
        rating: 5,
        testimonialAllowed: true,
      },
    ));
    expect(within(dialog).getByRole('status')).toHaveTextContent(/сообщение сохранено/i);
  });

  it('uses regular support for an account-wide Founder subscription without a current-project claim', async () => {
    vi.useRealTimers();
    vi.mocked(api.getBillingSubscription).mockResolvedValue({
      subscription: {
        ...activeSubscription,
        plan_code: 'founder_14d',
        access_kind: 'founder',
        auto_renew: false,
        next_renewal_at: null,
      },
    });
    vi.mocked(api.getProjectPublication).mockResolvedValue(
      restoredPublication(['https://example.com']),
    );
    vi.mocked(api.createCustomerContact).mockResolvedValue({
      request_id: 'support-other-project',
      accepted: true,
    });

    render(<UpgradeGate {...gateProps} projectId="project-without-founder-grant" />);

    expect(await screen.findByRole('heading', { name: 'Виджет опубликован' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Связаться с Kaigo' }));
    expect(screen.getByRole('dialog', { name: 'Связаться с Kaigo' })).toBeVisible();
    expect(screen.queryByRole('radio')).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: 'Сообщение' }), {
      target: { value: 'Нужна помощь с публикацией другого проекта.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }));

    await waitFor(() => expect(api.createCustomerContact).toHaveBeenCalledWith(
      'project-without-founder-grant',
      'Нужна помощь с публикацией другого проекта.',
      'csrf-billing',
      'checkout-request-123',
      {
        kind: 'support',
        rating: undefined,
        testimonialAllowed: false,
      },
    ));
  });

  it('keeps one contact key across close and reopen, then resets it after success', async () => {
    vi.useRealTimers();
    vi.mocked(crypto.randomUUID)
      .mockReturnValueOnce('11111111-1111-4111-8111-111111111111')
      .mockReturnValueOnce('22222222-2222-4222-8222-222222222222');
    vi.mocked(api.getBillingSubscription).mockResolvedValue({
      subscription: activeSubscription,
    });
    vi.mocked(api.createCustomerContact)
      .mockRejectedValueOnce(new TypeError('network failed'))
      .mockResolvedValueOnce({ request_id: 'contact-1', accepted: true })
      .mockResolvedValueOnce({ request_id: 'contact-2', accepted: true });

    render(<UpgradeGate {...gateProps} />);
    await waitFor(() => expect(screen.getByRole('button', {
      name: 'Связаться с Kaigo',
    })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Связаться с Kaigo' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Сообщение' }), {
      target: { value: 'Помогите установить виджет на основной сайт.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }));

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(
      /текст сохранён в форме/i,
    ));
    fireEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
    fireEvent.click(screen.getByRole('button', { name: 'Связаться с Kaigo' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Сообщение' }), {
      target: { value: 'Помогите установить виджет на основной сайт.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }));
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(
      /сообщение сохранено/i,
    ));

    expect(api.createCustomerContact).toHaveBeenNthCalledWith(
      1,
      'project-123',
      'Помогите установить виджет на основной сайт.',
      'csrf-billing',
      '11111111-1111-4111-8111-111111111111',
      {
        kind: 'support',
        rating: undefined,
        testimonialAllowed: false,
      },
    );
    expect(api.createCustomerContact).toHaveBeenNthCalledWith(
      2,
      'project-123',
      'Помогите установить виджет на основной сайт.',
      'csrf-billing',
      '11111111-1111-4111-8111-111111111111',
      {
        kind: 'support',
        rating: undefined,
        testimonialAllowed: false,
      },
    );

    fireEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
    fireEvent.click(screen.getByRole('button', { name: 'Связаться с Kaigo' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Сообщение' }), {
      target: { value: 'Теперь нужна помощь с другим доменом.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }));

    await waitFor(() => expect(api.createCustomerContact).toHaveBeenCalledTimes(3));
    expect(api.createCustomerContact).toHaveBeenNthCalledWith(
      3,
      'project-123',
      'Теперь нужна помощь с другим доменом.',
      'csrf-billing',
      '22222222-2222-4222-8222-222222222222',
      {
        kind: 'support',
        rating: undefined,
        testimonialAllowed: false,
      },
    );
  });

  it('starts a new contact key when the failed request fields change', async () => {
    vi.useRealTimers();
    vi.mocked(crypto.randomUUID)
      .mockReturnValueOnce('33333333-3333-4333-8333-333333333333')
      .mockReturnValueOnce('44444444-4444-4444-8444-444444444444');
    vi.mocked(api.getBillingSubscription).mockResolvedValue({
      subscription: activeSubscription,
    });
    vi.mocked(api.createCustomerContact)
      .mockRejectedValueOnce(new TypeError('network failed'))
      .mockResolvedValueOnce({ request_id: 'contact-2', accepted: true });

    render(<UpgradeGate {...gateProps} />);
    await waitFor(() => expect(screen.getByRole('button', {
      name: 'Связаться с Kaigo',
    })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Связаться с Kaigo' }));
    const message = screen.getByRole('textbox', { name: 'Сообщение' });
    fireEvent.change(message, {
      target: { value: 'Помогите установить виджет на основной сайт.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }));
    await waitFor(() => expect(screen.getByRole('alert')).toBeVisible());

    fireEvent.change(message, {
      target: { value: 'Помогите установить виджет на второй сайт.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }));
    await waitFor(() => expect(api.createCustomerContact).toHaveBeenCalledTimes(2));

    expect(vi.mocked(api.createCustomerContact).mock.calls.map((call) => call[3])).toEqual([
      '33333333-3333-4333-8333-333333333333',
      '44444444-4444-4444-8444-444444444444',
    ]);
  });

  it('shows the exact next server-priced charge and date', async () => {
    vi.mocked(api.getBillingSubscription).mockResolvedValue({
      subscription: {
        ...activeSubscription,
        plan_code: 'starter_intro_15d',
        next_charge: {
          plan_code: 'starter_intro_balance_15d',
          amount_minor: 150_000,
          currency: 'RUB',
          period_days: 15,
          at: '2026-08-28T12:00:00Z',
        },
      },
    });

    render(<UpgradeGate {...gateProps} />);
    await act(async () => Promise.resolve());

    expect(screen.getByText(/следующее списание — 1 500 ₽ 28 августа 2026/i)).toBeVisible();
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
    expect(screen.getByText(/тариф действует до/i)).toBeVisible();
    expect(screen.queryByText('Всё готово к публикации')).not.toBeInTheDocument();
  });

  it('keeps domain configuration out of the customer publication flow', async () => {
    vi.useRealTimers();
    vi.mocked(api.getBillingSubscription).mockResolvedValue({
      subscription: activeSubscription,
    });

    render(
      <UpgradeGate
        {...gateProps}
        sourceUrl="https://Example.COM/products/widget?campaign=private"
      />,
    );

    expect(await screen.findByRole('heading', { name: 'Виджет опубликован' })).toBeVisible();
    expect(screen.queryByLabelText('На каких сайтах разрешить виджет')).not.toBeInTheDocument();
  });

  it('auto-publishes an active subscription exactly once across rerenders', async () => {
    vi.useRealTimers();
    vi.mocked(api.getBillingSubscription).mockResolvedValue({
      subscription: activeSubscription,
    });

    const view = render(<UpgradeGate {...gateProps} />);

    await waitFor(() => expect(api.publishProject).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole('heading', { name: 'Виджет опубликован' })).toBeVisible();
    expect(screen.queryByText('Всё готово к публикации')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {
      name: 'Опубликовать и получить код',
    })).not.toBeInTheDocument();

    view.rerender(<UpgradeGate {...gateProps} sourceUrl="https://example.com/updated" />);
    await act(async () => Promise.resolve());

    expect(api.publishProject).toHaveBeenCalledTimes(1);
  });

  it('waits for billing recovery for the current project before auto-publishing after a project switch', async () => {
    vi.useRealTimers();
    type BillingSnapshot = Awaited<ReturnType<typeof api.getBillingSubscription>>;
    let resolveSecondBilling!: (value: BillingSnapshot) => void;
    vi.mocked(api.getBillingSubscription)
      .mockResolvedValueOnce({ subscription: activeSubscription })
      .mockImplementationOnce(() => new Promise<BillingSnapshot>((resolve) => {
        resolveSecondBilling = resolve;
      }));

    const view = render(<UpgradeGate {...gateProps} projectId="project-a" />);
    await waitFor(() => expect(api.publishProject).toHaveBeenCalledWith(
      'project-a',
      expect.anything(),
      'csrf-billing',
    ));
    vi.mocked(api.publishProject).mockClear();

    view.rerender(<UpgradeGate {...gateProps} projectId="project-b" />);
    await waitFor(() => expect(api.getBillingSubscription).toHaveBeenCalledTimes(2));
    await act(async () => Promise.resolve());
    expect(api.publishProject).not.toHaveBeenCalled();

    await act(async () => {
      resolveSecondBilling({ subscription: null });
      await Promise.resolve();
    });
    expect(await screen.findByRole('heading', {
      name: '14 дней полностью бесплатно',
    })).toBeVisible();
    expect(api.publishProject).not.toHaveBeenCalled();
  });

  it('recovers an existing publication when revisiting a project in the same mounted gate', async () => {
    vi.useRealTimers();
    vi.mocked(api.getBillingSubscription).mockResolvedValue({ subscription: activeSubscription });
    vi.mocked(api.getProjectPublication).mockReset()
      .mockResolvedValueOnce({ publication: null })
      .mockResolvedValueOnce({ publication: null })
      .mockResolvedValueOnce(restoredPublication(['https://example.com']));

    const view = render(<UpgradeGate {...gateProps} projectId="project-a" />);
    await waitFor(() => expect(api.publishProject).toHaveBeenCalledTimes(1));

    view.rerender(<UpgradeGate {...gateProps} projectId="project-b" />);
    await waitFor(() => expect(api.publishProject).toHaveBeenCalledTimes(2));

    view.rerender(<UpgradeGate {...gateProps} projectId="project-a" />);
    await waitFor(() => expect(api.getProjectPublication).toHaveBeenCalledTimes(3));
    expect(await screen.findByRole('heading', { name: 'Виджет опубликован' })).toBeVisible();
    expect(api.publishProject).toHaveBeenCalledTimes(2);
  });

  it('rejects a late A recovery after an A to B to A project cycle', async () => {
    vi.useRealTimers();
    type PublicationRecovery = Awaited<ReturnType<typeof api.getProjectPublication>>;
    let resolveFirstA!: (value: PublicationRecovery) => void;
    let resolveSecondA!: (value: PublicationRecovery) => void;
    let aRequests = 0;
    vi.mocked(api.getBillingSubscription).mockResolvedValue({ subscription: activeSubscription });
    vi.mocked(api.getProjectPublication).mockReset().mockImplementation((requestedProjectId) => {
      if (requestedProjectId === 'project-b') return Promise.resolve({ publication: null });
      aRequests += 1;
      return new Promise<PublicationRecovery>((resolve) => {
        if (aRequests === 1) resolveFirstA = resolve;
        else resolveSecondA = resolve;
      });
    });

    const view = render(<UpgradeGate {...gateProps} projectId="project-a" />);
    await waitFor(() => expect(api.getProjectPublication).toHaveBeenCalledWith('project-a'));

    view.rerender(<UpgradeGate {...gateProps} projectId="project-b" />);
    await waitFor(() => expect(api.publishProject).toHaveBeenCalledWith(
      'project-b',
      expect.anything(),
      'csrf-billing',
    ));

    view.rerender(<UpgradeGate {...gateProps} projectId="project-a" />);
    await waitFor(() => expect(aRequests).toBe(2));
    vi.mocked(api.publishProject).mockClear();

    await act(async () => {
      resolveFirstA({ publication: null });
      await Promise.resolve();
    });
    expect(api.publishProject).not.toHaveBeenCalled();

    await act(async () => {
      resolveSecondA(restoredPublication(['https://a.example']));
      await Promise.resolve();
    });
    expect(await screen.findByRole('heading', { name: 'Виджет опубликован' })).toBeVisible();
    expect(api.publishProject).not.toHaveBeenCalled();
  });

  it('ignores a checkout created for a project that is no longer active', async () => {
    vi.useRealTimers();
    vi.mocked(crypto.randomUUID)
      .mockReturnValueOnce('11111111-1111-4111-8111-111111111111')
      .mockReturnValueOnce('22222222-2222-4222-8222-222222222222');
    type CheckoutResult = Awaited<ReturnType<typeof api.createBillingCheckout>>;
    let resolveCheckout!: (value: CheckoutResult) => void;
    const replace = vi.fn();
    const close = vi.fn();
    const paymentWindow = {
      location: { replace },
      close,
      closed: false,
      opener: window,
    } as unknown as Window;
    vi.spyOn(window, 'open').mockReturnValue(paymentWindow);
    vi.mocked(api.createBillingCheckout).mockImplementation(() => new Promise<CheckoutResult>(
      (resolve) => {
        resolveCheckout = resolve;
      },
    ));

    const view = render(<UpgradeGate {...gateProps} projectId="project-a" />);
    expect(await screen.findByRole('button', { name: /выбрать месяц/i })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: /выбрать месяц/i }));
    await waitFor(() => expect(api.createBillingCheckout).toHaveBeenCalledWith(
      'starter_monthly',
      'csrf-billing',
      '11111111-1111-4111-8111-111111111111',
      'project-a',
      false,
    ));

    view.rerender(<UpgradeGate {...gateProps} projectId="project-b" />);
    expect(await screen.findByRole('heading', {
      name: '14 дней полностью бесплатно',
    })).toBeVisible();

    await act(async () => {
      resolveCheckout({
        payment,
        checkout_url: 'https://yoomoney.ru/checkout/payment-123',
        created: true,
      });
      await Promise.resolve();
    });

    expect(replace).not.toHaveBeenCalled();
    expect(close).toHaveBeenCalledOnce();
    expect(screen.getByRole('heading', {
      name: '14 дней полностью бесплатно',
    })).toBeVisible();

    vi.mocked(api.createBillingCheckout).mockResolvedValue({
      payment: { ...payment, id: 'payment-b' },
      checkout_url: 'https://yoomoney.ru/checkout/payment-b',
      created: true,
    });
    fireEvent.click(screen.getByRole('button', { name: /выбрать месяц/i }));
    await waitFor(() => expect(api.createBillingCheckout).toHaveBeenNthCalledWith(
      2,
      'starter_monthly',
      'csrf-billing',
      '22222222-2222-4222-8222-222222222222',
      'project-b',
      false,
    ));
  });

  it('ignores a stale publication-conflict reload after switching projects', async () => {
    vi.useRealTimers();
    type PublicationRecovery = Awaited<ReturnType<typeof api.getProjectPublication>>;
    let resolveConflictReload!: (value: PublicationRecovery) => void;
    vi.mocked(api.getBillingSubscription)
      .mockResolvedValueOnce({ subscription: activeSubscription })
      .mockResolvedValueOnce({ subscription: null });
    vi.mocked(api.getProjectPublication).mockReset()
      .mockResolvedValueOnce({ publication: null })
      .mockImplementationOnce(() => new Promise<PublicationRecovery>((resolve) => {
        resolveConflictReload = resolve;
      }));
    vi.mocked(api.publishProject).mockRejectedValue(new api.BuilderApiError(
      'Publication changed; reload before retrying',
      {
        status: 409,
        code: 'publication_conflict',
        raw: 'Publication changed; reload before retrying',
      },
    ));

    const view = render(<UpgradeGate {...gateProps} projectId="project-a" />);
    await waitFor(() => expect(api.getProjectPublication).toHaveBeenCalledTimes(2));

    view.rerender(<UpgradeGate {...gateProps} projectId="project-b" />);
    expect(await screen.findByRole('heading', {
      name: '14 дней полностью бесплатно',
    })).toBeVisible();

    await act(async () => {
      resolveConflictReload(restoredPublication(['https://example.com']));
      await Promise.resolve();
    });

    expect(screen.queryByText(/публикация изменилась в другой сессии/i)).not.toBeInTheDocument();
    expect(screen.getByRole('heading', {
      name: '14 дней полностью бесплатно',
    })).toBeVisible();
  });

  it('waits for null publication recovery before auto-publishing active access once', async () => {
    vi.useRealTimers();
    vi.mocked(api.getBillingSubscription).mockResolvedValue({ subscription: activeSubscription });
    type PublicationRecovery = Awaited<ReturnType<typeof api.getProjectPublication>>;
    let resolvePublicationRecovery!: (value: PublicationRecovery) => void;
    vi.mocked(api.getProjectPublication).mockImplementation(() => new Promise<PublicationRecovery>(
      (resolve) => {
        resolvePublicationRecovery = resolve;
      },
    ));

    const view = render(<UpgradeGate {...gateProps} />);

    await waitFor(() => expect(api.getProjectPublication).toHaveBeenCalledOnce());
    expect(screen.getByRole('heading', {
      name: 'Восстанавливаем публикацию',
    })).toBeVisible();
    expect(api.publishProject).not.toHaveBeenCalled();
    view.rerender(<UpgradeGate {...gateProps} sourceUrl="https://example.com/while-recovering" />);
    await act(async () => Promise.resolve());
    expect(api.publishProject).not.toHaveBeenCalled();

    await act(async () => {
      resolvePublicationRecovery({ publication: null });
      await Promise.resolve();
    });

    await waitFor(() => expect(api.publishProject).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole('heading', { name: 'Виджет опубликован' })).toBeVisible();
    view.rerender(<UpgradeGate {...gateProps} sourceUrl="https://example.com/after-recovery" />);
    await act(async () => Promise.resolve());
    expect(api.publishProject).toHaveBeenCalledTimes(1);
  });

  it('waits for restored publication recovery and never auto-publishes it', async () => {
    vi.useRealTimers();
    vi.mocked(api.getBillingSubscription).mockResolvedValue({ subscription: activeSubscription });
    type PublicationRecovery = Awaited<ReturnType<typeof api.getProjectPublication>>;
    let resolvePublicationRecovery!: (value: PublicationRecovery) => void;
    vi.mocked(api.getProjectPublication).mockImplementation(() => new Promise<PublicationRecovery>(
      (resolve) => {
        resolvePublicationRecovery = resolve;
      },
    ));

    const view = render(<UpgradeGate {...gateProps} />);

    await waitFor(() => expect(api.getProjectPublication).toHaveBeenCalledOnce());
    expect(api.publishProject).not.toHaveBeenCalled();
    await act(async () => {
      resolvePublicationRecovery(restoredPublication(['https://example.com']));
      await Promise.resolve();
    });

    expect(await screen.findByRole('heading', { name: 'Виджет опубликован' })).toBeVisible();
    expect(api.publishProject).not.toHaveBeenCalled();
    view.rerender(<UpgradeGate {...gateProps} projectVersionId="version-5" />);
    await act(async () => Promise.resolve());
    expect(api.publishProject).not.toHaveBeenCalled();
  });

  it('omits allowed domains from the first publication payload', async () => {
    vi.useRealTimers();
    vi.mocked(api.getBillingSubscription).mockResolvedValue({
      subscription: activeSubscription,
    });
    vi.mocked(api.publishProject).mockResolvedValue({
      publication_id: 'publication-123',
      release_id: 'release-4',
      artifact_id: 'artifact-123',
      project_version_id: 'version-4',
      stable_key: 'stable-widget',
      revision: 4,
      allowed_domains: ['https://example.com'],
      checksum: 'checksum-4',
      embed_url: 'https://widgets.kaigo.space/embed/stable-widget.js',
      runtime_url: 'https://widgets.kaigo.space/runtime/stable-widget',
    });

    render(
      <UpgradeGate
        {...gateProps}
        sourceUrl="https://Example.COM/products/widget?campaign=private"
      />,
    );

    await waitFor(() => expect(api.publishProject).toHaveBeenCalledWith(
      'project-123',
      {
        project_version_id: 'version-4',
        expected_active_release_id: null,
      },
      'csrf-billing',
    ));
  });

  it.each([
    [
      'project source URL has no safe HTTPS origin',
      'Не удалось определить адрес сайта проекта. Проверьте, что в проекте указана публичная HTTPS-ссылка.',
    ],
    [
      'artifact is not publishable',
      'Эта версия виджета пока не готова к публикации. Выберите проверенную версию или пересоздайте виджет.',
    ],
  ])('explains a permanent publication rejection without exposing backend text: %s', async (raw, expected) => {
    vi.useRealTimers();
    vi.mocked(api.getBillingSubscription).mockResolvedValue({
      subscription: activeSubscription,
    });
    vi.mocked(api.publishProject).mockRejectedValue(new api.BuilderApiError(
      raw,
      {
        status: 422,
        code: 'publication_invalid',
        raw,
      },
    ));

    render(<UpgradeGate {...gateProps} />);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(expected);
    expect(alert).not.toHaveTextContent(raw);
  });

  it('keeps the retry message for an unknown temporary publication failure', async () => {
    vi.useRealTimers();
    vi.mocked(api.getBillingSubscription).mockResolvedValue({
      subscription: activeSubscription,
    });
    vi.mocked(api.publishProject).mockRejectedValue(new api.BuilderApiError(
      'upstream unavailable',
      {
        status: 503,
        code: 'provider_unavailable',
        raw: 'upstream unavailable',
        retryable: true,
      },
    ));

    render(<UpgradeGate {...gateProps} />);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Не удалось опубликовать виджет. Попробуйте ещё раз.');
    expect(alert).not.toHaveTextContent('upstream unavailable');
  });

  it('does not loop a failed active-subscription auto-publication after rerender', async () => {
    vi.useRealTimers();
    vi.mocked(api.getBillingSubscription).mockResolvedValue({ subscription: activeSubscription });
    vi.mocked(api.publishProject).mockRejectedValue(new api.BuilderApiError('invalid body', {
      status: 400,
      code: 'invalid_body',
      raw: 'invalid body',
    }));

    const view = render(<UpgradeGate {...gateProps} />);

    expect(await screen.findByRole('heading', {
      name: 'Доступ подключён, публикация не завершена',
    })).toBeVisible();
    await waitFor(() => expect(api.publishProject).toHaveBeenCalledTimes(1));

    view.rerender(<UpgradeGate {...gateProps} sourceUrl="https://example.com/updated" />);
    await act(async () => Promise.resolve());

    expect(api.publishProject).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'Повторить публикацию' })).toBeEnabled();
  });

  it('publishes the selected accepted artifact, shows a stable embed snippet, and rolls back a known prior release', async () => {
    vi.useRealTimers();
    vi.mocked(api.getBillingSubscription).mockResolvedValue({
      subscription: activeSubscription,
    });
    vi.mocked(api.publishProject)
      .mockResolvedValueOnce({
        publication_id: 'publication-123',
        release_id: 'release-4',
        artifact_id: 'artifact-123',
        project_version_id: 'version-4',
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
        project_version_id: 'version-5',
        stable_key: 'stable-widget',
        revision: 5,
        allowed_domains: ['https://example.com'],
        checksum: 'checksum-5',
        embed_url: 'https://widgets.kaigo.space/embed/stable-widget.js',
        runtime_url: 'https://widgets.kaigo.space/runtime/stable-widget',
      });
    vi.mocked(api.rollbackPublication).mockResolvedValue({
      publication_id: 'publication-123',
      release_id: 'release-4',
      artifact_id: 'artifact-123',
      project_version_id: 'version-4',
      stable_key: 'stable-widget',
      revision: 4,
      allowed_domains: ['https://example.com'],
      checksum: 'checksum-4',
      embed_url: 'https://widgets.kaigo.space/embed/stable-widget.js',
      runtime_url: 'https://widgets.kaigo.space/runtime/stable-widget',
    });

    const view = render(<UpgradeGate {...gateProps} />);
    await waitFor(() => expect(api.publishProject).toHaveBeenCalledTimes(1));

    expect(api.publishProject).toHaveBeenNthCalledWith(
      1,
      'project-123',
      {
        project_version_id: 'version-4',
        expected_active_release_id: null,
      },
      'csrf-billing',
    );
    const firstSnippet = screen.getByText(
      '<script src="https://widgets.kaigo.space/embed/stable-widget.js" async></script>',
    );
    expect(firstSnippet).not.toBeVisible();
    fireEvent.click(screen.getByText('Код для разработчика'));
    expect(firstSnippet).toBeVisible();
    expect(screen.getByRole('heading', { name: 'Виджет опубликован' })).toBeVisible();
    const firstHandoff = screen.getByRole('region', { name: 'Установите виджет на сайт' });
    expect(within(firstHandoff).getByRole('button', {
      name: 'Скопировать код установки',
    })).toBeEnabled();
    expect(within(firstHandoff).getByRole('button', {
      name: 'Скопировать ссылку загрузчика',
    })).toBeEnabled();
    expect(within(firstHandoff).getByRole('link', {
      name: 'Открыть инструкцию по установке',
    })).toHaveAttribute('href', '/install');
    expect(screen.queryByRole('list', { name: 'Путь до запуска виджета' })).not.toBeInTheDocument();
    expect(screen.queryByText(/дорабат/i)).not.toBeInTheDocument();

    view.rerender(
      <UpgradeGate
        {...gateProps}
        projectVersionId="version-5"
        projectVersionOrdinal={5}
        artifactId="artifact-456"
        revision={5}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Обновить публикацию' }));
    await act(async () => {
      await Promise.resolve();
    });
    expect(api.publishProject).toHaveBeenNthCalledWith(
      2,
      'project-123',
      {
        project_version_id: 'version-5',
        expected_active_release_id: 'release-4',
        allowed_domains: ['https://example.com'],
      },
      'csrf-billing',
    );

    fireEvent.click(screen.getByRole('button', { name: 'Вернуть предыдущую публикацию' }));
    await act(async () => {
      await Promise.resolve();
    });
    expect(api.rollbackPublication).toHaveBeenCalledWith(
      'publication-123',
      'release-4',
      'release-5',
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
          project_version_id: 'version-5',
          previous_release_id: 'release-4',
          revision: 7,
          checksum: 'checksum-5',
          created_at: '2026-07-28T13:00:00Z',
        },
        releases: [
          {
            release_id: 'release-4',
            artifact_id: 'artifact-123',
            project_version_id: 'version-4',
            previous_release_id: null,
            revision: 7,
            checksum: 'checksum-4',
            created_at: '2026-07-28T12:00:00Z',
          },
          {
            release_id: 'release-5',
            artifact_id: 'artifact-456',
            project_version_id: 'version-5',
            previous_release_id: 'release-4',
            revision: 7,
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
      project_version_id: 'version-4',
      stable_key: 'stable-widget',
      revision: 7,
      allowed_domains: ['https://example.com', 'https://shop.example.com'],
      checksum: 'checksum-4',
      embed_url: 'https://widgets.kaigo.space/embed/stable-widget.js',
      runtime_url: 'https://widgets.kaigo.space/runtime/stable-widget',
    });

    render(
      <UpgradeGate
        {...gateProps}
        projectVersions={[
          { id: 'version-5', ordinal: 5 },
          { id: 'version-4', ordinal: 4 },
        ]}
      />,
    );
    const hydratedSnippet = await screen.findByText(
      '<script src="https://widgets.kaigo.space/embed/stable-widget.js" async></script>',
    );
    expect(hydratedSnippet).not.toBeVisible();
    fireEvent.click(screen.getByText('Код для разработчика'));
    expect(hydratedSnippet).toBeVisible();
    expect(screen.queryByLabelText('На каких сайтах разрешить виджет')).not.toBeInTheDocument();

    expect(screen.getByRole('status')).toHaveTextContent(
      'Версия 5 опубликована и доступна на разрешённых сайтах.',
    );
    fireEvent.click(screen.getByRole('button', {
      name: 'Вернуть версию 4',
    }));
    await act(async () => {
      await Promise.resolve();
    });
    expect(api.rollbackPublication).toHaveBeenCalledWith(
      'publication-123',
      'release-4',
      'release-5',
      'csrf-billing',
    );
  });

  it('ignores a rollback result from a project that is no longer active', async () => {
    vi.useRealTimers();
    type PublicationRecovery = Awaited<ReturnType<typeof api.getProjectPublication>>;
    type RollbackResult = Awaited<ReturnType<typeof api.rollbackPublication>>;
    let resolveRollback!: (value: RollbackResult) => void;
    const baseA = restoredPublication(['https://a.example']);
    const projectA: PublicationRecovery = {
      publication: {
        ...baseA.publication,
        active_release: {
          ...baseA.publication.active_release,
          previous_release_id: 'release-a-3',
        },
        releases: [
          {
            release_id: 'release-a-3',
            artifact_id: 'artifact-a-3',
            project_version_id: 'version-a-3',
            previous_release_id: null,
            revision: 3,
            checksum: 'checksum-a-3',
            created_at: '2026-07-27T13:00:00Z',
          },
        ],
      },
    };
    const baseB = restoredPublication(['https://b.example']);
    const projectB: PublicationRecovery = {
      publication: {
        ...baseB.publication,
        publication_id: 'publication-b',
        stable_key: 'stable-widget-b',
        embed_url: 'https://widgets.kaigo.space/embed/stable-widget-b.js',
        runtime_url: 'https://widgets.kaigo.space/runtime/stable-widget-b',
        active_release: {
          ...baseB.publication.active_release,
          release_id: 'release-b-4',
          artifact_id: 'artifact-b-4',
          project_version_id: 'version-b-4',
          checksum: 'checksum-b-4',
        },
      },
    };
    vi.mocked(api.getBillingSubscription).mockResolvedValue({ subscription: activeSubscription });
    vi.mocked(api.getProjectPublication).mockReset()
      .mockResolvedValueOnce(projectA)
      .mockResolvedValueOnce(projectB);
    vi.mocked(api.rollbackPublication).mockImplementation(() => new Promise<RollbackResult>(
      (resolve) => {
        resolveRollback = resolve;
      },
    ));

    const view = render(<UpgradeGate {...gateProps} projectId="project-a" />);
    fireEvent.click(await screen.findByRole('button', {
      name: 'Вернуть предыдущую публикацию',
    }));
    await waitFor(() => expect(api.rollbackPublication).toHaveBeenCalledOnce());

    view.rerender(<UpgradeGate {...gateProps} projectId="project-b" />);
    expect(await screen.findByText(
      '<script src="https://widgets.kaigo.space/embed/stable-widget-b.js" async></script>',
    )).not.toBeVisible();

    await act(async () => {
      resolveRollback({
        publication_id: 'publication-123',
        release_id: 'release-a-3',
        artifact_id: 'artifact-a-3',
        project_version_id: 'version-a-3',
        stable_key: 'stable-widget',
        revision: 3,
        allowed_domains: ['https://a.example'],
        checksum: 'checksum-a-3',
        embed_url: 'https://widgets.kaigo.space/embed/stable-widget.js',
        runtime_url: 'https://widgets.kaigo.space/runtime/stable-widget',
      });
      await Promise.resolve();
    });

    expect(screen.getByText(
      '<script src="https://widgets.kaigo.space/embed/stable-widget-b.js" async></script>',
    )).not.toBeVisible();
    expect(screen.queryByText(
      '<script src="https://widgets.kaigo.space/embed/stable-widget.js" async></script>',
    )).not.toBeInTheDocument();
  });

  it('shows the installation handoff while keeping developer details collapsed', async () => {
    vi.useRealTimers();
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    vi.mocked(api.getBillingSubscription).mockResolvedValue({ subscription: activeSubscription });
    vi.mocked(api.getProjectPublication).mockResolvedValue({
      publication: {
        publication_id: 'publication-123',
        stable_key: 'stable-widget',
        state: 'published',
        allowed_domains: ['https://example.com'],
        embed_url: 'https://widgets.kaigo.space/embed/stable-widget.js',
        runtime_url: 'https://widgets.kaigo.space/runtime/stable-widget',
        active_release: {
          release_id: 'release-5',
          artifact_id: 'artifact-456',
          project_version_id: 'version-5',
          previous_release_id: null,
          revision: 7,
          checksum: 'checksum-5',
          created_at: '2026-07-28T13:00:00Z',
        },
        releases: [],
      },
    });

    render(
      <UpgradeGate
        {...gateProps}
        projectVersionId="version-5"
        projectVersionOrdinal={5}
        projectVersions={[{ id: 'version-5', ordinal: 5 }]}
      />,
    );

    expect(await screen.findByRole('heading', { name: 'Виджет опубликован' })).toBeVisible();
    expect(screen.queryByLabelText('На каких сайтах разрешить виджет')).not.toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Путь до запуска виджета' })).not.toBeInTheDocument();
    const handoff = screen.getByRole('region', { name: 'Установите виджет на сайт' });
    expect(within(handoff).getByText('Остался один шаг')).toBeVisible();
    expect(within(handoff).getByText(
      /передайте его человеку, который управляет сайтом/i,
    )).toBeVisible();
    expect(within(handoff).getByRole('button', {
      name: 'Скопировать код установки',
    })).toBeVisible();
    expect(within(handoff).getByRole('button', {
      name: 'Скопировать ссылку загрузчика',
    })).toBeVisible();
    expect(within(handoff).getByRole('link', {
      name: 'Открыть инструкцию по установке',
    })).toHaveAttribute(
      'href',
      '/install',
    );
    expect(screen.queryByText(/Stable embed URL/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/HTTPS origin/i)).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent('starter_monthly');

    const developerDisclosure = screen.getByText('Код для разработчика').closest('details');
    const snippet = screen.getByText(
      '<script src="https://widgets.kaigo.space/embed/stable-widget.js" async></script>',
    );
    expect(developerDisclosure).not.toHaveAttribute('open');
    expect(snippet).not.toBeVisible();

    fireEvent.click(screen.getByRole('button', { name: 'Скопировать код установки' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(
      '<script src="https://widgets.kaigo.space/embed/stable-widget.js" async></script>',
    ));
    expect(await screen.findByText('Код скопирован. Его можно отправить разработчику.')).toBeVisible();

    fireEvent.click(screen.getByRole('button', { name: 'Скопировать ссылку загрузчика' }));
    await waitFor(() => expect(writeText).toHaveBeenNthCalledWith(
      2,
      'https://widgets.kaigo.space/embed/stable-widget.js',
    ));
    expect(await screen.findByText(
      /ссылка(?: загрузчика)? скопирована/i,
      { selector: '[role="status"]' },
    )).toBeVisible();
    expect(screen.queryByText('Код скопирован. Его можно отправить разработчику.')).not.toBeInTheDocument();

    fireEvent.click(screen.getByText('Код для разработчика'));
    expect(snippet).toBeVisible();
  });

  it('reveals both manual-copy values when Clipboard access is rejected', async () => {
    vi.useRealTimers();
    const writeText = vi.fn().mockRejectedValue(new Error('clipboard denied'));
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    vi.mocked(api.getBillingSubscription).mockResolvedValue({ subscription: activeSubscription });
    vi.mocked(api.getProjectPublication).mockResolvedValue(
      restoredPublication(['https://example.com']),
    );

    render(<UpgradeGate {...gateProps} />);

    expect(await screen.findByRole('heading', { name: 'Виджет опубликован' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Скопировать ссылку загрузчика' }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith(
      'https://widgets.kaigo.space/embed/stable-widget.js',
    ));
    expect(await screen.findByRole('alert')).toHaveTextContent(/скопируйте вручную/i);
    expectManualCopyValueVisible(
      '<script src="https://widgets.kaigo.space/embed/stable-widget.js" async></script>',
    );
    expectManualCopyValueVisible(
      'https://widgets.kaigo.space/embed/stable-widget.js',
    );
  });

  it('keeps the latest copy action authoritative when Clipboard promises finish out of order', async () => {
    vi.useRealTimers();
    let rejectCode!: (reason?: unknown) => void;
    let resolveLink!: () => void;
    const writeText = vi.fn()
      .mockImplementationOnce(() => new Promise<void>((_resolve, reject) => {
        rejectCode = reject;
      }))
      .mockImplementationOnce(() => new Promise<void>((resolve) => {
        resolveLink = resolve;
      }));
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    vi.mocked(api.getBillingSubscription).mockResolvedValue({ subscription: activeSubscription });
    vi.mocked(api.getProjectPublication).mockResolvedValue(
      restoredPublication(['https://example.com']),
    );

    render(<UpgradeGate {...gateProps} />);

    expect(await screen.findByRole('heading', { name: 'Виджет опубликован' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Скопировать код установки' }));
    fireEvent.click(screen.getByRole('button', { name: 'Скопировать ссылку загрузчика' }));

    await act(async () => {
      resolveLink();
      await Promise.resolve();
    });
    expect(screen.getByText('Ссылка загрузчика скопирована.')).toBeVisible();

    await act(async () => {
      rejectCode(new Error('older clipboard request failed'));
      await Promise.resolve();
    });
    expect(screen.getByText('Ссылка загрузчика скопирована.')).toBeVisible();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('reveals both manual-copy values when Clipboard access is unavailable', async () => {
    vi.useRealTimers();
    vi.stubGlobal('navigator', {});
    vi.mocked(api.getBillingSubscription).mockResolvedValue({ subscription: activeSubscription });
    vi.mocked(api.getProjectPublication).mockResolvedValue(
      restoredPublication(['https://example.com']),
    );

    render(<UpgradeGate {...gateProps} />);

    expect(await screen.findByRole('heading', { name: 'Виджет опубликован' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Скопировать код установки' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/скопируйте вручную/i);
    expectManualCopyValueVisible(
      '<script src="https://widgets.kaigo.space/embed/stable-widget.js" async></script>',
    );
    expectManualCopyValueVisible(
      'https://widgets.kaigo.space/embed/stable-widget.js',
    );
  });

  it.each([
    ['the exact restored allowlist', ['https://example.com', 'https://shop.example.com']],
    ['an empty restored deny-all allowlist', []],
  ])('preserves %s when updating a publication', async (_label, allowedDomains) => {
    vi.useRealTimers();
    vi.mocked(api.getBillingSubscription).mockResolvedValue({ subscription: activeSubscription });
    vi.mocked(api.getProjectPublication).mockResolvedValue(restoredPublication(allowedDomains));
    vi.mocked(api.publishProject).mockResolvedValue(updatedPublication(allowedDomains));

    render(<UpgradeGate {...gateProps} />);

    const updateAction = await screen.findByRole('button', { name: 'Обновить публикацию' });
    await waitFor(() => expect(updateAction).toBeEnabled());
    fireEvent.click(updateAction);

    await waitFor(() => expect(api.publishProject).toHaveBeenCalledWith(
      'project-123',
      {
        project_version_id: 'version-4',
        expected_active_release_id: 'release-4',
        allowed_domains: allowedDomains,
      },
      'csrf-billing',
    ));
  });

  it('never auto-updates a restored publication, even when the selected version changes', async () => {
    vi.useRealTimers();
    vi.mocked(api.getBillingSubscription).mockResolvedValue({ subscription: activeSubscription });
    vi.mocked(api.getProjectPublication).mockResolvedValue(
      restoredPublication(['https://example.com']),
    );

    const view = render(<UpgradeGate {...gateProps} />);
    expect(await screen.findByRole('heading', { name: 'Виджет опубликован' })).toBeVisible();
    expect(api.publishProject).not.toHaveBeenCalled();

    view.rerender(
      <UpgradeGate
        {...gateProps}
        projectVersionId="version-5"
        projectVersionOrdinal={5}
        artifactId="artifact-456"
        revision={5}
      />,
    );
    await act(async () => Promise.resolve());

    expect(api.publishProject).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Обновить публикацию' })).toBeEnabled();
  });

  it('fails closed when publication restoration fails', async () => {
    vi.useRealTimers();
    vi.mocked(api.getBillingSubscription).mockResolvedValue({ subscription: activeSubscription });
    vi.mocked(api.getProjectPublication).mockRejectedValue(new Error('publication state unavailable'));

    render(<UpgradeGate {...gateProps} />);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/обновите страницу/i);
    expect(alert).not.toHaveTextContent(/повторная публикация остаётся доступна/i);
    expect(screen.getByRole('button', { name: /опубликовать/i })).toBeDisabled();
    expect(api.publishProject).not.toHaveBeenCalled();
  });

  it('reloads authoritative publication state after a CAS conflict without retrying', async () => {
    vi.useRealTimers();
    vi.mocked(api.getBillingSubscription).mockResolvedValue({ subscription: activeSubscription });
    vi.mocked(api.getProjectPublication)
      .mockResolvedValueOnce({ publication: null })
      .mockResolvedValueOnce({
        publication: {
          publication_id: 'publication-123',
          stable_key: 'stable-widget',
          state: 'published',
          allowed_domains: ['https://other.example.com'],
          embed_url: 'https://widgets.kaigo.space/embed/stable-widget.js',
          runtime_url: 'https://widgets.kaigo.space/runtime/stable-widget',
          active_release: {
            release_id: 'release-other',
            artifact_id: 'artifact-other',
            project_version_id: 'version-other',
            previous_release_id: null,
            revision: 7,
            checksum: 'checksum-other',
            created_at: '2026-07-30T12:00:00Z',
          },
          releases: [],
        },
      });
    vi.mocked(api.publishProject).mockRejectedValue(new api.BuilderApiError(
      'Publication changed; reload before retrying',
      {
        status: 409,
        code: 'publication_conflict',
        raw: 'Publication changed; reload before retrying',
      },
    ));

    render(<UpgradeGate {...gateProps} />);
    await waitFor(() => expect(api.getProjectPublication).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(api.getProjectPublication).toHaveBeenCalledTimes(2));
    expect(api.publishProject).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Публикация изменилась в другой сессии. Данные обновлены — проверьте их и повторите действие.',
    );
    expect(screen.getByText(
      '<script src="https://widgets.kaigo.space/embed/stable-widget.js" async></script>',
    )).not.toBeVisible();
    expect(screen.queryByLabelText('На каких сайтах разрешить виджет')).not.toBeInTheDocument();
  });

  it('fails closed when a CAS conflict cannot reload authoritative publication state', async () => {
    vi.useRealTimers();
    vi.mocked(api.getBillingSubscription).mockResolvedValue({ subscription: activeSubscription });
    vi.mocked(api.getProjectPublication)
      .mockResolvedValueOnce({ publication: null })
      .mockRejectedValueOnce(new Error('publication reload unavailable'));
    vi.mocked(api.publishProject).mockRejectedValue(new api.BuilderApiError(
      'Publication changed; reload before retrying',
      {
        status: 409,
        code: 'publication_conflict',
        raw: 'Publication changed; reload before retrying',
      },
    ));

    render(<UpgradeGate {...gateProps} />);

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Публикация изменилась в другой сессии, но не удалось обновить её состояние.',
    );
    const retry = screen.getByRole('button', {
      name: /(?:повторить публикацию|опубликовать виджет после восстановления данных)/i,
    });
    expect(retry).toBeDisabled();
    fireEvent.click(retry);
    expect(api.publishProject).toHaveBeenCalledTimes(1);
  });

  it('blocks rollback when an existing publication conflict cannot be reloaded', async () => {
    vi.useRealTimers();
    vi.mocked(api.getBillingSubscription).mockResolvedValue({ subscription: activeSubscription });
    vi.mocked(api.getProjectPublication)
      .mockResolvedValueOnce({
        publication: {
          publication_id: 'publication-123',
          stable_key: 'stable-widget',
          state: 'published',
          allowed_domains: ['https://example.com'],
          embed_url: 'https://widgets.kaigo.space/embed/stable-widget.js',
          runtime_url: 'https://widgets.kaigo.space/runtime/stable-widget',
          active_release: {
            release_id: 'release-5',
            artifact_id: 'artifact-456',
            project_version_id: 'version-5',
            previous_release_id: 'release-4',
            revision: 7,
            checksum: 'checksum-5',
            created_at: '2026-07-28T13:00:00Z',
          },
          releases: [
            {
              release_id: 'release-4',
              artifact_id: 'artifact-123',
              project_version_id: 'version-4',
              previous_release_id: null,
              revision: 7,
              checksum: 'checksum-4',
              created_at: '2026-07-28T12:00:00Z',
            },
          ],
        },
      })
      .mockRejectedValueOnce(new Error('publication reload unavailable'));
    vi.mocked(api.publishProject).mockRejectedValue(new api.BuilderApiError(
      'Publication changed; reload before retrying',
      {
        status: 409,
        code: 'publication_conflict',
        raw: 'Publication changed; reload before retrying',
      },
    ));

    render(<UpgradeGate {...gateProps} />);

    expect(await screen.findByRole('heading', { name: 'Виджет опубликован' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Обновить публикацию' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Публикация изменилась в другой сессии, но не удалось обновить её состояние.',
    );
    expect(screen.getByRole('button', { name: 'Обновить публикацию' })).toBeDisabled();
    const rollback = screen.getByRole('button', {
      name: /вернуть (?:предыдущую публикацию|версию)/i,
    });
    expect(rollback).toBeDisabled();
    fireEvent.click(rollback);
    expect(api.rollbackPublication).not.toHaveBeenCalled();
  });

  it('keeps the artifact publication contract only when project versions are unavailable', async () => {
    vi.useRealTimers();
    vi.mocked(api.getBillingSubscription).mockResolvedValue({ subscription: activeSubscription });
    vi.mocked(api.publishProject).mockResolvedValue({
      publication_id: 'publication-legacy',
      release_id: 'release-legacy',
      artifact_id: 'artifact-123',
      project_version_id: null,
      stable_key: 'stable-legacy',
      revision: 4,
      allowed_domains: ['https://example.com'],
      checksum: 'checksum-legacy',
      embed_url: 'https://widgets.kaigo.space/embed/stable-legacy.js',
      runtime_url: 'https://widgets.kaigo.space/runtime/stable-legacy',
    });

    render(
      <UpgradeGate
        {...gateProps}
        versionsEnabled={false}
        projectVersionId={undefined}
        projectVersionOrdinal={undefined}
      />,
    );
    await waitFor(() => expect(api.publishProject).toHaveBeenCalledWith(
      'project-123',
      {
        artifact_id: 'artifact-123',
        revision: 4,
      },
      'csrf-billing',
    ));
  });

  it('fails closed when initial billing recovery fails and retries the check explicitly', async () => {
    vi.useRealTimers();
    vi.mocked(api.getBillingSubscription)
      .mockRejectedValueOnce(new api.BuilderApiError('Unavailable', {
        status: 503,
        code: 'billing_unavailable',
        raw: 'Unavailable',
        retryable: true,
      }))
      .mockResolvedValueOnce({ subscription: null });

    render(<UpgradeGate csrfToken="csrf-billing" projectId="project-123" />);
    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.queryByRole('button', { name: /выбрать условия публикации/i })).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(/не удалось проверить тариф/i);
    fireEvent.click(screen.getByRole('button', { name: /повторить проверку/i }));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(api.getBillingSubscription).toHaveBeenCalledTimes(2);
    expect(await screen.findByRole('button', {
      name: 'Активировать бесплатно и продолжить',
    })).toBeEnabled();
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
    expect(screen.queryByRole('button', { name: /выбрать условия публикации/i })).not.toBeInTheDocument();
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

    render(<UpgradeGate csrfToken="csrf-billing" projectId="project-123" />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    await chooseMonthlyPlan();

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
