import { Check, Clock, Star, X } from '@phosphor-icons/react';
import { useEffect, useState } from 'react';

import type { BillingOffer } from './types';

interface PublicationOfferDialogProps {
  open: boolean;
  offer: BillingOffer;
  busy: boolean;
  loading?: boolean;
  initialIntroConsent?: boolean;
  error: string | null;
  onClose: () => void;
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

const BUTTON_LABELS: Record<string, string> = {
  starter_intro_15d: 'Выбрать 15 дней',
  starter_monthly: 'Выбрать месяц',
  starter_quarterly: 'Выбрать квартал',
};

export function PublicationOfferDialog({
  open,
  offer,
  busy,
  loading = false,
  initialIntroConsent = false,
  error,
  onClose,
  onFounder,
  onCheckout,
}: PublicationOfferDialogProps) {
  const [introConsent, setIntroConsent] = useState(initialIntroConsent);

  useEffect(() => {
    if (!open) {
      setIntroConsent(initialIntroConsent);
    }
  }, [initialIntroConsent, open]);
  if (!open) return null;

  const introPlan = offer.plans.find((plan) => plan.code === 'starter_intro_15d');
  const footnote = introPlan
    ? `Стартовые ${introPlan.period_days} ${daysWord(introPlan.period_days)} доступны один раз. Без галочки вы платите только ${rubles(introPlan.amount_minor)}, и доступ закончится через ${introPlan.period_days} ${daysWord(introPlan.period_days)}. Все цены и даты подтверждает сервер.`
    : 'Все цены и даты подтверждает сервер.';

  const choose = (code: string) => {
    const isIntro = code === 'starter_intro_15d';
    onCheckout(code, isIntro && introConsent);
  };

  return (
    <div className="publication-offer__backdrop" role="presentation" onMouseDown={(event) => {
      if (event.currentTarget === event.target && !busy) onClose();
    }}>
      <section
        className="publication-offer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="publication-offer-title"
        data-publication-flow="modal"
      >
        <header className="publication-offer__header">
          <div>
            <span>ПУБЛИКАЦИЯ</span>
            <h2 id="publication-offer-title">Опубликовать виджет</h2>
            <p>Выберите способ проверить его на настоящем сайте. Результат в Студии в любом случае останется у вас.</p>
          </div>
          <button type="button" className="publication-offer__close" onClick={onClose} disabled={busy} aria-label="Закрыть">
            <X aria-hidden size={20} />
          </button>
        </header>

        <section className="publication-offer__access" aria-labelledby="publication-offer-access-title">
          <span>ДОСТУП К ПУБЛИКАЦИИ</span>
          <h3 id="publication-offer-access-title">Выберите, как открыть виджет на сайте</h3>
          <p>Founder-пилот или платный тариф — условия видны заранее, а результат останется в Студии.</p>
        </section>

        {loading && <div className="publication-offer__loading" role="status"><Clock aria-hidden size={18} /> Загружаем доступные условия…</div>}
        {!loading && offer.founder.eligible && (
          <article className="publication-offer__founder">
            <div className="publication-offer__founder-mark"><Star aria-hidden size={21} weight="fill" /></div>
            <div>
              <span>ДЛЯ ПЕРВЫХ КЛИЕНТОВ · ОСТАЛОСЬ {offer.founder.remaining} МЕСТ</span>
              <h3>{offer.founder.period_days} {daysWord(offer.founder.period_days)} бесплатно</h3>
              <p>Founder-пилот: опубликуем виджет бесплатно на {offer.founder.period_days} {daysWord(offer.founder.period_days)}. Взамен попросим честную обратную связь о работе, недостатках и нужных доработках. Без карты и автосписаний.</p>
              <ul>
                <li><Check aria-hidden /> Публикация на вашем домене</li>
                <li><Check aria-hidden /> {new Intl.NumberFormat('ru-RU').format(offer.founder.generation_tokens)} токенов</li>
                <li><Check aria-hidden /> Никакого платежа через {offer.founder.period_days} {daysWord(offer.founder.period_days)}</li>
              </ul>
            </div>
            <button type="button" onClick={onFounder} disabled={busy}>
              {busy ? <Clock aria-hidden size={18} /> : null} Активировать {offer.founder.period_days} {daysWord(offer.founder.period_days)} и опубликовать
            </button>
          </article>
        )}

        {!loading && <>
          <section className="publication-offer__paid-intro" aria-labelledby="publication-offer-paid-title">
            <div>
              <span>ЕСЛИ НУЖНО СРАЗУ</span>
              <h3 id="publication-offer-paid-title">Или выберите платный тариф</h3>
            </div>
            <p>Публикация включится сразу после оплаты — без ожидания Founder-слота.</p>
          </section>
          <div className="publication-offer__plans">
            {offer.plans.map((plan) => {
              const isIntro = plan.code === 'starter_intro_15d';
              return (
                <article key={plan.code} className={isIntro ? 'publication-offer__plan publication-offer__plan--accent' : 'publication-offer__plan'}>
                  <div>
                    <span>{isIntro ? 'ПОПРОБОВАТЬ НА САЙТЕ' : plan.period_days === 90 ? 'ВЫГОДНЕЕ НА 3 МЕСЯЦА' : 'БЕЗ АВТОПРОДЛЕНИЯ'}</span>
                    <h3>{rubles(plan.amount_minor)}</h3>
                    <p>за {plan.period_days} {daysWord(plan.period_days)} · {new Intl.NumberFormat('ru-RU').format(plan.generation_tokens)} токенов</p>
                  </div>
                  {isIntro && plan.renewal && plan.renewal.following && (
                    <label className="publication-offer__consent">
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
                    {BUTTON_LABELS[plan.code] ?? 'Выбрать тариф'}
                  </button>
                </article>
              );
            })}
          </div>
        </>}
        {error && <p className="publication-offer__error" role="alert">{error}</p>}
        <p className="publication-offer__footnote">{footnote}</p>
      </section>
    </div>
  );
}
