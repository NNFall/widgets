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
  it('explains the access decision in the publication modal', () => {
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

    const dialog = screen.getByRole('dialog', { name: /опубликовать виджет/i });
    expect(dialog).toHaveAttribute('data-publication-flow', 'modal');
    expect(screen.getByRole('heading', {
      name: 'Выберите, как открыть виджет на сайте',
    })).toBeVisible();
    expect(screen.getByText(/Founder-пилот или платный тариф/i)).toBeVisible();
    expect(screen.getAllByRole('article')).toHaveLength(4);
  });

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
    expect(screen.getByText(/после оплаченных 15 дней — 1 500 ₽ за следующие 15 дней/i)).toBeVisible();
    expect(screen.getByText(/затем 2 000 ₽ каждые 30 дней/i)).toBeVisible();
    expect(screen.getByText(/стартовые 15 дней доступны один раз/i)).toBeVisible();
  });

  it('derives intro renewal copy from the server periods and amounts', () => {
    const customIntroOffer: BillingOffer = {
      ...offer,
      plans: [
        {
          ...offer.plans[0],
          amount_minor: 75_000,
          period_days: 7,
          renewal: {
            ...offer.plans[0].renewal!,
            amount_minor: 125_000,
            period_days: 21,
            following: {
              ...offer.plans[0].renewal!.following!,
              amount_minor: 350_000,
              period_days: 45,
            },
          },
        },
        ...offer.plans.slice(1),
      ],
    };

    render(
      <PublicationOfferDialog
        open
        offer={customIntroOffer}
        busy={false}
        error={null}
        onClose={vi.fn()}
        onFounder={vi.fn()}
        onCheckout={vi.fn()}
      />,
    );

    expect(screen.getByText(/после оплаченных 7 дней — 1 250 ₽ за следующие 21 день/i)).toBeVisible();
    expect(screen.getByText(/затем 3 500 ₽ каждые 45 дней/i)).toBeVisible();
    expect(screen.getByText(
      'Стартовые 7 дней доступны один раз. Без галочки вы платите только 750 ₽, и доступ закончится через 7 дней. Все цены и даты подтверждает сервер.',
    )).toBeVisible();
  });

  it('keeps only the neutral server-priced footnote when intro is hidden', () => {
    const offerWithoutIntro = {
      ...offer,
      plans: offer.plans.filter((plan) => plan.code !== 'starter_intro_15d'),
    };

    render(
      <PublicationOfferDialog
        open
        offer={offerWithoutIntro}
        busy={false}
        error={null}
        onClose={vi.fn()}
        onFounder={vi.fn()}
        onCheckout={vi.fn()}
      />,
    );

    expect(screen.getByText('Все цены и даты подтверждает сервер.')).toBeVisible();
    expect(screen.queryByText(/стартовые/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/без галочки/i)).not.toBeInTheDocument();
  });

  it('keeps renewal optional for the 500-ruble offer', () => {
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
    expect(onCheckout).toHaveBeenNthCalledWith(1, 'starter_intro_15d', false);
    fireEvent.click(screen.getByRole('checkbox', { name: /после оплаченных 15 дней/i }));
    fireEvent.click(screen.getByRole('button', { name: /выбрать 15 дней/i }));
    expect(onCheckout).toHaveBeenNthCalledWith(2, 'starter_intro_15d', true);
  });

  it('can open the existing intro consent for a read-only presentation', () => {
    const onCheckout = vi.fn();
    render(
      <PublicationOfferDialog
        open
        offer={offer}
        busy={false}
        error={null}
        initialIntroConsent
        onClose={vi.fn()}
        onFounder={vi.fn()}
        onCheckout={onCheckout}
      />,
    );

    expect(screen.getByRole('checkbox', { name: /после оплаченных 15 дней/i })).toBeChecked();
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
