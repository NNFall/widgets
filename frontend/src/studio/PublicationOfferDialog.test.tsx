import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach } from 'vitest';
import { describe, expect, it, vi } from 'vitest';

import { PublicationOfferDialog } from './PublicationOfferDialog';
import type { BillingOffer } from './types';

afterEach(cleanup);

const offer: BillingOffer = {
  founder: {
    eligible: true,
    reason: null,
    remaining: 7,
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
      renewal: { plan_code: 'starter_monthly', amount_minor: 200_000, currency: 'RUB', period_days: 30 },
    },
    {
      code: 'starter_monthly', title: 'Kaigo Starter, 1 месяц', amount_minor: 200_000,
      currency: 'RUB', period_days: 30, generation_tokens: 1_000_000, renewal: null,
    },
    {
      code: 'starter_quarterly', title: 'Kaigo Starter, 3 месяца', amount_minor: 500_000,
      currency: 'RUB', period_days: 90, generation_tokens: 3_000_000, renewal: null,
    },
  ],
};

describe('PublicationOfferDialog', () => {
  it('explains the founder grant and exposes all three server-priced offers', () => {
    render(
      <PublicationOfferDialog
        open
        offer={offer}
        busy={false}
        error={null}
        onClose={vi.fn()}
        onFounder={vi.fn()}
        onCheckout={vi.fn()}
      />,
    );

    expect(screen.getByRole('dialog', { name: /опубликовать виджет/i })).toBeVisible();
    expect(screen.getByText(/14 дней бесплатно/i)).toBeVisible();
    expect(screen.getByText(/без карты и автосписаний/i)).toBeVisible();
    expect(screen.getByRole('button', { name: /активировать 14 дней/i })).toBeEnabled();
    expect(screen.getByText('500 ₽')).toBeVisible();
    expect(screen.getByText('2 000 ₽')).toBeVisible();
    expect(screen.getByText('5 000 ₽')).toBeVisible();
  });

  it('requires explicit monthly-renewal consent before starting the 500-ruble offer', () => {
    const onCheckout = vi.fn();
    render(
      <PublicationOfferDialog
        open
        offer={offer}
        busy={false}
        error={null}
        onClose={vi.fn()}
        onFounder={vi.fn()}
        onCheckout={onCheckout}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /выбрать 15 дней/i }));
    expect(onCheckout).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent(/подтвердите переход/i);
    fireEvent.click(screen.getByRole('checkbox', { name: /после 15 дней/i }));
    fireEvent.click(screen.getByRole('button', { name: /выбрать 15 дней/i }));
    expect(onCheckout).toHaveBeenCalledWith('starter_intro_15d', true);
  });

  it('does not require auto-renew for the direct monthly and quarterly choices', () => {
    const onCheckout = vi.fn();
    render(
      <PublicationOfferDialog
        open
        offer={offer}
        busy={false}
        error={null}
        onClose={vi.fn()}
        onFounder={vi.fn()}
        onCheckout={onCheckout}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /выбрать месяц/i }));
    fireEvent.click(screen.getByRole('button', { name: /выбрать квартал/i }));
    expect(onCheckout).toHaveBeenNthCalledWith(1, 'starter_monthly', false);
    expect(onCheckout).toHaveBeenNthCalledWith(2, 'starter_quarterly', false);
  });
});
