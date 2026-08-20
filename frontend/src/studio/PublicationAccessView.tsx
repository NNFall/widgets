import { Check, Clock, Star } from '@phosphor-icons/react';
import { useEffect, useState } from 'react';

import type { BillingOffer } from './types';

export interface PublicationAccessViewProps {
  offer: BillingOffer;
  busy: boolean;
  loading: boolean;
  initialIntroConsent?: boolean;
  error: string | null;
  onFounder: () => void;
  onCheckout: (planCode: string, autoRenew: boolean) => void;
}

function rubles(amountMinor: number) {
  return `${new Intl.NumberFormat('ru-RU').format(amountMinor / 100)} ₽`;
}

function daysWord(value: number) {
  const absolute = Math.abs(value) % 100;
  if (absolute >= 11 && absolute <= 14) return 'дней';
  switch (absolute % 10) {
    case 1:
      return 'день';
    case 2:
    case 3:
    case 4:
      return 'дня';
    default:
      return 'дней';
  }
}

function planButtonLabel(periodDays: number | undefined) {
  if (typeof periodDays !== 'number' || periodDays <= 0) return 'Выбрать тариф';
  if (periodDays === 30) return 'Выбрать месяц';
  if (periodDays === 90) return 'Выбрать квартал';
  return `Выбрать ${periodDays} ${daysWord(periodDays)}`;
}

export function PublicationAccessView({
  offer,
  busy,
  loading = false,
  initialIntroConsent = false,
  error,
  onFounder,
  onCheckout,
}: PublicationAccessViewProps) {
  const [introConsent, setIntroConsent] = useState(initialIntroConsent);

  useEffect(() => {
    setIntroConsent(initialIntroConsent);
  }, [initialIntroConsent]);

  const introPlan = offer.plans.find((plan) => plan.code === 'starter_intro_15d');
  const footnote = introPlan
    ? `Стартовые ${introPlan.period_days} ${daysWord(introPlan.period_days)} доступны один раз. Без галочки вы платите только ${rubles(introPlan.amount_minor)}, и доступ закончится через ${introPlan.period_days} ${daysWord(introPlan.period_days)}. Все цены и даты подтверждает сервер.`
    : 'Все цены и даты подтверждает сервер.';

  const choose = (code: string) => {
    const isIntro = code === 'starter_intro_15d';
    onCheckout(code, isIntro && introConsent);
  };

  const periodLabel = `${offer.founder.period_days} ${daysWord(offer.founder.period_days)}`;
  const hasFounder = offer.founder.eligible;

  return (
    <section className="publication-access" aria-labelledby="publication-access-title">
      {loading && (
        <div className="publication-access__loading" role="status">
          <Clock aria-hidden size={18} />
          <h2 id="publication-access-title">Загружаем доступные условия…</h2>
        </div>
      )}

      {!loading && hasFounder && (
        <article className="publication-access__founder">
          <div className="publication-access__founder-mark">
            <Star aria-hidden size={21} weight="fill" />
          </div>
          <div>
            <span>FOUNDER-ПИЛОТ · ДЛЯ ПЕРВЫХ КЛИЕНТОВ · ОСТАЛОСЬ {offer.founder.remaining} МЕСТ</span>
            <h2 id="publication-access-title">
              {periodLabel} полностью бесплатно
            </h2>
            <p>
              Мы бесплатно откроем публикацию без карты и автосписаний. Взамен попросим
              честно рассказать, что удобно, чего не хватает и что стоит доработать.
            </p>
            <ul>
              <li><Check aria-hidden /> Публикация на вашем домене</li>
              <li><Check aria-hidden /> {new Intl.NumberFormat('ru-RU').format(offer.founder.generation_tokens)} токенов</li>
              <li><Check aria-hidden /> Никакого платежа через {periodLabel}</li>
            </ul>
          </div>
          <button type="button" onClick={onFounder} disabled={busy}>
            {busy ? <Clock aria-hidden size={18} /> : null} Активировать бесплатно и продолжить
          </button>
        </article>
      )}

      {!loading && (
        <>
          <div className="publication-access__paid-intro">
            <div>
              <span>{hasFounder ? 'ЕСЛИ НУЖНО СРАЗУ' : 'ТАРИФЫ ПУБЛИКАЦИИ'}</span>
              {hasFounder ? (
                <h3>Или сразу выбрать платный тариф</h3>
              ) : (
                <h2 id="publication-access-title">Выберите платный тариф</h2>
              )}
            </div>
            <p>Публикация включится сразу после оплаты{hasFounder ? ' — без ожидания Founder-слота' : ''}.</p>
          </div>
          <div className="publication-access__plans">
            {offer.plans.map((plan) => {
              const isIntro = plan.code === 'starter_intro_15d';
              return (
                <article
                  key={plan.code}
                  className={isIntro ? 'publication-access__plan publication-access__plan--accent' : 'publication-access__plan'}
                >
                  <div>
                    <span>{isIntro ? 'ПОПРОБОВАТЬ НА САЙТЕ' : plan.period_days === 90 ? 'ВЫГОДНЕЕ НА 3 МЕСЯЦА' : 'БЕЗ АВТОПРОДЛЕНИЯ'}</span>
                    <h3>{rubles(plan.amount_minor)}</h3>
                    <p>за {plan.period_days} {daysWord(plan.period_days)} · {new Intl.NumberFormat('ru-RU').format(plan.generation_tokens)} токенов</p>
                  </div>
                  {isIntro && plan.renewal && plan.renewal.following && (
                    <label className="publication-access__consent">
                      <input
                        type="checkbox"
                        checked={introConsent}
                        onChange={(event) => setIntroConsent(event.target.checked)}
                        disabled={busy}
                      />
                      <span>
                        Включить автопродление: после оплаченных {plan.period_days} {daysWord(plan.period_days)} — {rubles(plan.renewal.amount_minor)} за следующие {plan.renewal.period_days} {daysWord(plan.renewal.period_days)}, затем {rubles(plan.renewal.following.amount_minor)} каждые {plan.renewal.following.period_days} {daysWord(plan.renewal.following.period_days)}. Можно отключить до следующего списания.
                      </span>
                    </label>
                  )}
                  <button type="button" onClick={() => choose(plan.code)} disabled={busy}>
                    {planButtonLabel(plan.period_days)}
                  </button>
                </article>
              );
            })}
          </div>
        </>
      )}

      {error && <p className="publication-access__error" role="alert">{error}</p>}
      <p className="publication-access__footnote">{footnote}</p>
    </section>
  );
}
