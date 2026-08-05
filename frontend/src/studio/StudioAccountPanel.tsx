import {
  ArrowRight,
  CalendarBlank,
  CheckCircle,
  Gauge,
  WarningCircle,
} from '@phosphor-icons/react';
import { useEffect, useState } from 'react';

import { getBillingSubscription } from './api';
import type { BillingSubscription } from './types';

type StudioAccountPanelProps = {
  onOpenPublication: () => void;
};

type AccountState =
  | { status: 'loading' }
  | { status: 'ready'; subscription: BillingSubscription | null }
  | { status: 'error' };

const tokenFormatter = new Intl.NumberFormat('ru-RU');
const dateFormatter = new Intl.DateTimeFormat('ru-RU', {
  day: 'numeric',
  month: 'long',
  year: 'numeric',
});

const PLAN_LABELS: Record<string, string> = {
  starter_monthly: 'Starter',
};

const PLAN_BENEFITS = [
  'Доработка новыми версиями',
  'Публикация на выбранных сайтах',
  'Код установки и безопасные обновления',
] as const;

function PlanBenefits({ active }: { active: boolean }) {
  return (
    <section className="studio-account__benefits" aria-labelledby="studio-account-benefits-title">
      <h4 id="studio-account-benefits-title">{active ? 'В тариф уже входит' : 'После оплаты откроется'}</h4>
      <ul>
        {PLAN_BENEFITS.map((benefit) => (
          <li key={benefit}>
            <CheckCircle aria-hidden size={19} weight="fill" />
            <span>{benefit}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function formatDate(value: string | null) {
  if (!value) return 'Дата появится после оплаты';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Дата уточняется' : dateFormatter.format(date);
}

export function StudioAccountPanel({ onOpenPublication }: StudioAccountPanelProps) {
  const [requestKey, setRequestKey] = useState(0);
  const [state, setState] = useState<AccountState>({ status: 'loading' });

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: 'loading' });
    void getBillingSubscription(controller.signal)
      .then(({ subscription }) => setState({ status: 'ready', subscription }))
      .catch((error: unknown) => {
        if (
          controller.signal.aborted
          || (error instanceof DOMException && error.name === 'AbortError')
        ) return;
        setState({ status: 'error' });
      });
    return () => controller.abort();
  }, [requestKey]);

  if (state.status === 'loading') {
    return (
      <section className="studio-account" aria-label="Тариф и лимиты">
        <div className="studio-account__loading" role="status">
          <span aria-hidden />
          <strong>Загружаю данные аккаунта…</strong>
          <p>Тариф и доступный объём появятся через несколько секунд.</p>
        </div>
      </section>
    );
  }

  if (state.status === 'error') {
    return (
      <section className="studio-account" aria-label="Тариф и лимиты">
        <div className="studio-account__notice studio-account__notice--error">
          <WarningCircle aria-hidden size={24} weight="fill" />
          <div>
            <strong>Не получилось загрузить тариф</strong>
            <p>Проверьте соединение и попробуйте ещё раз.</p>
          </div>
        </div>
        <button type="button" className="studio-account__secondary" onClick={() => setRequestKey((value) => value + 1)}>
          Повторить
        </button>
      </section>
    );
  }

  const { subscription } = state;
  if (!subscription) {
    return (
      <section className="studio-account" aria-label="Тариф и лимиты">
        <div className="studio-account__hero">
          <span className="studio-account__icon"><Gauge aria-hidden size={24} weight="fill" /></span>
          <div>
            <p>Текущий режим</p>
            <h3>Бесплатный режим</h3>
          </div>
        </div>
        <div className="studio-account__notice">
          <CheckCircle aria-hidden size={24} weight="fill" />
          <div>
            <strong>Первая экспресс-версия — бесплатно</strong>
            <p>Можно собрать один виджет и посмотреть результат до оплаты.</p>
          </div>
        </div>
        <div className="studio-account__limit-card">
          <span>Доработки и публикация</span>
          <strong>Для продолжения нужен тариф</strong>
          <p>Тариф открывает новые версии, публикацию и установку на сайт.</p>
        </div>
        <PlanBenefits active={false} />
        <button type="button" className="studio-account__primary" onClick={onOpenPublication}>
          Посмотреть тарифы <ArrowRight aria-hidden size={18} weight="bold" />
        </button>
      </section>
    );
  }

  const remaining = subscription.generation_tokens_remaining;
  const planLabel = PLAN_LABELS[subscription.plan_code] ?? subscription.plan_code;
  const renewalDate = subscription.next_renewal_at ?? subscription.current_period_end;

  return (
    <section className="studio-account" aria-label="Тариф и лимиты">
      <div className="studio-account__hero">
        <span className="studio-account__icon"><Gauge aria-hidden size={24} weight="fill" /></span>
        <div>
          <p>Ваш тариф</p>
          <h3>{planLabel}</h3>
        </div>
        <span className="studio-account__status" data-status={subscription.status}>
          {subscription.status === 'active' ? 'Активен' : 'Требует внимания'}
        </span>
      </div>

      <div className="studio-account__limit-card studio-account__limit-card--accent">
        <span>Осталось на доработки</span>
        <strong>{typeof remaining === 'number' ? tokenFormatter.format(remaining) : 'Без ограничений'}</strong>
        <p>Токены расходуются только при создании новой версии виджета.</p>
      </div>

      <div className="studio-account__renewal">
        <CalendarBlank aria-hidden size={22} />
        <div>
          <strong>{subscription.auto_renew ? 'Автопродление включено' : 'Автопродление выключено'}</strong>
          <p>{subscription.auto_renew ? `Следующее продление — ${formatDate(renewalDate)}` : `Доступ оплачен до ${formatDate(subscription.current_period_end)}`}</p>
        </div>
      </div>

      <PlanBenefits active />

      <button type="button" className="studio-account__primary" onClick={onOpenPublication}>
        Управлять подпиской и публикацией <ArrowRight aria-hidden size={18} weight="bold" />
      </button>
    </section>
  );
}
