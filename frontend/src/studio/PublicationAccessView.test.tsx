import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { PublicationAccessView } from './PublicationAccessView';
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

describe('PublicationAccessView', () => {
  it('renders an embedded semantic section without dialog, modal, backdrop or close button', () => {
    render(
      <PublicationAccessView
        offer={offer}
        busy={false}
        loading={false}
        error={null}
        onFounder={vi.fn()}
        onCheckout={vi.fn()}
      />,
    );

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /закрыть/i })).not.toBeInTheDocument();
    expect(document.querySelector('.publication-offer__backdrop')).not.toBeInTheDocument();

    const heading = screen.getByRole('heading', {
      name: '14 дней полностью бесплатно',
    });
    expect(heading).toBeVisible();
    expect(heading.id).toBe('publication-access-title');

    const section = document.querySelector('section[aria-labelledby="publication-access-title"]');
    expect(section).toBeInTheDocument();
    expect(screen.getByText(/FOUNDER-ПИЛОТ · ДЛЯ ПЕРВЫХ КЛИЕНТОВ/i)).toBeVisible();
    expect(screen.getByText(/Мы бесплатно откроем публикацию без карты и автосписаний/i)).toBeVisible();
    expect(screen.getByText(/честно рассказать, что удобно, чего не хватает и что стоит доработать/i)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Активировать бесплатно и продолжить' })).toBeEnabled();
    expect(screen.getAllByRole('article')).toHaveLength(4);
  });

  it('explains the founder grant with exact copy, limits, places and exposes all server-priced offers', () => {
    render(
      <PublicationAccessView
        offer={offer}
        busy={false}
        loading={false}
        error={null}
        onFounder={vi.fn()}
        onCheckout={vi.fn()}
      />,
    );

    expect(screen.getByRole('heading', { name: '14 дней полностью бесплатно' })).toBeVisible();
    expect(screen.getByText(/7 мест/i)).toBeVisible();
    expect(screen.getByText(/1 500 000 токенов/i)).toBeVisible();
    expect(screen.getByText(/никакого платежа через 14 дней/i)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Активировать бесплатно и продолжить' })).toBeEnabled();
    expect(screen.getByRole('heading', { name: 'Или сразу выбрать платный тариф' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Выбрать 15 дней' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Выбрать месяц' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Выбрать квартал' })).toBeVisible();
    expect(screen.getByText('500 ₽')).toBeVisible();
    expect(screen.getByText('2 000 ₽')).toBeVisible();
    expect(screen.getByText('5 000 ₽')).toBeVisible();
    expect(screen.getByText(/после оплаченных 15 дней — 1 500 ₽ за следующие 15 дней/i)).toBeVisible();
    expect(screen.getByText(/затем 2 000 ₽ каждые 30 дней/i)).toBeVisible();
    expect(screen.getByText(/стартовые 15 дней доступны один раз/i)).toBeVisible();
  });

  it('derives intro renewal copy and dynamic button labels from server periods', () => {
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
      <PublicationAccessView
        offer={customIntroOffer}
        busy={false}
        loading={false}
        error={null}
        onFounder={vi.fn()}
        onCheckout={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: 'Выбрать 7 дней' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Выбрать месяц' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Выбрать квартал' })).toBeVisible();
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
      <PublicationAccessView
        offer={offerWithoutIntro}
        busy={false}
        loading={false}
        error={null}
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
      <PublicationAccessView
        offer={offer}
        busy={false}
        loading={false}
        error={null}
        onFounder={vi.fn()}
        onCheckout={onCheckout}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Выбрать 15 дней' }));
    expect(onCheckout).toHaveBeenNthCalledWith(1, 'starter_intro_15d', false);
    fireEvent.click(screen.getByRole('checkbox', { name: /после оплаченных 15 дней/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать 15 дней' }));
    expect(onCheckout).toHaveBeenNthCalledWith(2, 'starter_intro_15d', true);
  });

  it('can open the existing intro consent for a read-only presentation', () => {
    const onCheckout = vi.fn();
    render(
      <PublicationAccessView
        offer={offer}
        busy={false}
        loading={false}
        error={null}
        initialIntroConsent
        onFounder={vi.fn()}
        onCheckout={onCheckout}
      />,
    );

    expect(screen.getByRole('checkbox', { name: /после оплаченных 15 дней/i })).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать 15 дней' }));
    expect(onCheckout).toHaveBeenCalledWith('starter_intro_15d', true);
  });

  it('does not require auto-renew for the direct monthly and quarterly choices', () => {
    const onCheckout = vi.fn();
    render(
      <PublicationAccessView
        offer={offer}
        busy={false}
        loading={false}
        error={null}
        onFounder={vi.fn()}
        onCheckout={onCheckout}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать месяц' }));
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать квартал' }));
    expect(onCheckout).toHaveBeenNthCalledWith(1, 'starter_monthly', false);
    expect(onCheckout).toHaveBeenNthCalledWith(2, 'starter_quarterly', false);
  });

  it('renders loading state with an accessible publication-access-title heading', () => {
    render(
      <PublicationAccessView
        offer={offer}
        busy={false}
        loading={true}
        error={null}
        onFounder={vi.fn()}
        onCheckout={vi.fn()}
      />,
    );

    const status = screen.getByRole('status');
    expect(status).toHaveTextContent(/Загружаем доступные условия…/i);
    const heading = screen.getByRole('heading', { name: 'Загружаем доступные условия…' });
    expect(heading).toBeVisible();
    expect(heading.id).toBe('publication-access-title');

    const labelledElements = document.querySelectorAll('[aria-labelledby]');
    expect(labelledElements.length).toBeGreaterThan(0);
    for (const el of labelledElements) {
      const id = el.getAttribute('aria-labelledby')!;
      expect(document.getElementById(id)).not.toBeNull();
    }
  });

  it('renders founder-ineligible state with grammatical main heading and exactly one region landmark', () => {
    const ineligibleOffer: BillingOffer = {
      ...offer,
      founder: {
        ...offer.founder,
        eligible: false,
        reason: 'capacity_reached',
      },
    };

    render(
      <PublicationAccessView
        offer={ineligibleOffer}
        busy={false}
        loading={false}
        error={null}
        onFounder={vi.fn()}
        onCheckout={vi.fn()}
      />,
    );

    expect(screen.queryByRole('heading', { name: /полностью бесплатно/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/FOUNDER-ПИЛОТ/i)).not.toBeInTheDocument();

    const mainHeading = screen.getByRole('heading', { name: 'Выберите платный тариф' });
    expect(mainHeading).toBeVisible();
    expect(mainHeading.id).toBe('publication-access-title');

    const regions = screen.getAllByRole('region');
    expect(regions).toHaveLength(1);
    expect(screen.getByRole('region', { name: 'Выберите платный тариф' })).toBeVisible();

    const labelledElements = document.querySelectorAll('[aria-labelledby]');
    expect(labelledElements.length).toBeGreaterThan(0);
    for (const el of labelledElements) {
      const id = el.getAttribute('aria-labelledby')!;
      expect(document.getElementById(id)).not.toBeNull();
    }
  });

  it('renders error message when error is provided', () => {
    render(
      <PublicationAccessView
        offer={offer}
        busy={false}
        loading={false}
        error="Не удалось загрузить условия публикации. Попробуйте ещё раз."
        onFounder={vi.fn()}
        onCheckout={vi.fn()}
      />,
    );

    expect(screen.getByRole('alert')).toHaveTextContent('Не удалось загрузить условия публикации. Попробуйте ещё раз.');
  });

  it('calls onFounder when the founder CTA is clicked', () => {
    const onFounder = vi.fn();
    render(
      <PublicationAccessView
        offer={offer}
        busy={false}
        loading={false}
        error={null}
        onFounder={onFounder}
        onCheckout={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Активировать бесплатно и продолжить' }));
    expect(onFounder).toHaveBeenCalledTimes(1);
  });

  it('disables all buttons and checkboxes when busy is true', () => {
    render(
      <PublicationAccessView
        offer={offer}
        busy={true}
        loading={false}
        error={null}
        onFounder={vi.fn()}
        onCheckout={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: /Активировать бесплатно и продолжить/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Выбрать 15 дней' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Выбрать месяц' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Выбрать квартал' })).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: /после оплаченных 15 дней/i })).toBeDisabled();
  });
});
