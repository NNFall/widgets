import { Check, Clock, Star, X } from '@phosphor-icons/react';
import { useEffect, useState } from 'react';

import type { BillingOffer } from './types';

interface PublicationOfferDialogProps {
  open: boolean;
  offer: BillingOffer;
  busy: boolean;
  loading?: boolean;
  error: string | null;
  onClose: () => void;
  onFounder: () => void;
  onCheckout: (planCode: string, autoRenew: boolean) => void;
}

function rubles(amountMinor: number) {
  return `${new Intl.NumberFormat('ru-RU').format(amountMinor / 100)} ₽`;
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
  error,
  onClose,
  onFounder,
  onCheckout,
}: PublicationOfferDialogProps) {
  const [introConsent, setIntroConsent] = useState(false);
  const [consentError, setConsentError] = useState(false);

  useEffect(() => {
    if (!open) {
      setIntroConsent(false);
      setConsentError(false);
    }
  }, [open]);
  if (!open) return null;

  const choose = (code: string) => {
    const isIntro = code === 'starter_intro_15d';
    if (isIntro && !introConsent) {
      setConsentError(true);
      return;
    }
    setConsentError(false);
    onCheckout(code, isIntro);
  };

  return (
    <div className="publication-offer__backdrop" role="presentation" onMouseDown={(event) => {
      if (event.currentTarget === event.target && !busy) onClose();
    }}>
      <section className="publication-offer" role="dialog" aria-modal="true" aria-labelledby="publication-offer-title">
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

        {loading && <div className="publication-offer__loading" role="status"><Clock aria-hidden size={18} /> Загружаем доступные условия…</div>}
        {!loading && offer.founder.eligible && (
          <article className="publication-offer__founder">
            <div className="publication-offer__founder-mark"><Star aria-hidden size={21} weight="fill" /></div>
            <div>
              <span>ДЛЯ ПЕРВЫХ КЛИЕНТОВ · ОСТАЛОСЬ {offer.founder.remaining} МЕСТ</span>
              <h3>14 дней бесплатно</h3>
              <p>Опубликуем виджет без карты и автосписаний. Внутри — до трёх стандартных доработок. Взамен попросим честную обратную связь.</p>
              <ul>
                <li><Check aria-hidden /> Публикация на вашем домене</li>
                <li><Check aria-hidden /> {new Intl.NumberFormat('ru-RU').format(offer.founder.generation_tokens)} токенов</li>
                <li><Check aria-hidden /> Никакого платежа через 14 дней</li>
              </ul>
            </div>
            <button type="button" onClick={onFounder} disabled={busy}>
              {busy ? <Clock aria-hidden size={18} /> : null} Активировать 14 дней и опубликовать
            </button>
          </article>
        )}

        {!loading && <div className="publication-offer__plans">
          {offer.plans.map((plan) => {
            const isIntro = plan.code === 'starter_intro_15d';
            return (
              <article key={plan.code} className={isIntro ? 'publication-offer__plan publication-offer__plan--accent' : 'publication-offer__plan'}>
                <div>
                  <span>{isIntro ? 'ПОПРОБОВАТЬ НА САЙТЕ' : plan.period_days === 90 ? 'ВЫГОДНЕЕ НА 3 МЕСЯЦА' : 'БЕЗ АВТОПРОДЛЕНИЯ'}</span>
                  <h3>{rubles(plan.amount_minor)}</h3>
                  <p>за {plan.period_days} дней · {new Intl.NumberFormat('ru-RU').format(plan.generation_tokens)} токенов</p>
                </div>
                {isIntro && plan.renewal && (
                  <label className="publication-offer__consent">
                    <input
                      type="checkbox"
                      checked={introConsent}
                      onChange={(event) => {
                        setIntroConsent(event.target.checked);
                        if (event.target.checked) setConsentError(false);
                      }}
                      disabled={busy}
                    />
                    <span>После 15 дней — {rubles(plan.renewal.amount_minor)} каждые {plan.renewal.period_days} дней. Можно отключить в любой момент.</span>
                  </label>
                )}
                <button type="button" onClick={() => choose(plan.code)} disabled={busy}>
                  {BUTTON_LABELS[plan.code] ?? 'Выбрать тариф'}
                </button>
              </article>
            );
          })}
        </div>}
        {consentError && <p className="publication-offer__error" role="alert">Подтвердите переход на 2 000 ₽ каждые 30 дней или выберите другой вариант.</p>}
        {error && <p className="publication-offer__error" role="alert">{error}</p>}
        <p className="publication-offer__footnote">Все цены и даты подтверждает сервер. Автопродление не включается без вашего отдельного согласия.</p>
      </section>
    </div>
  );
}
