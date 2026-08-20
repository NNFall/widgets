import {
  ArrowRight,
  CheckCircle,
  Clock,
  Copy,
  Sparkle,
} from '@phosphor-icons/react';

import { KaigoLogo } from '../shared/KaigoLogo';
import { PublicationOfferDialog } from './PublicationOfferDialog';
import { SupportDialog } from './SupportDialog';
import type { BillingOffer } from './types';

export type PresentationScenario =
  | 'index'
  | 'publication-start'
  | 'founder-offer'
  | 'intro-no-renew'
  | 'intro-auto-renew'
  | 'all-plans'
  | 'payment-pending'
  | 'founder-active'
  | 'subscription-active'
  | 'published'
  | 'feedback'
  | 'tariff-limits';

const INTRO_PLAN = {
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
} satisfies BillingOffer['plans'][number];

const ALL_PLANS: BillingOffer['plans'] = [
  INTRO_PLAN,
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
];

function offer(founder: boolean, plans = ALL_PLANS): BillingOffer {
  return {
    founder: {
      eligible: founder,
      reason: founder ? null : 'presentation',
      remaining: 20,
      capacity: 20,
      period_days: 14,
      generation_tokens: 1_500_000,
    },
    plans,
  };
}

const LINKS: Array<[PresentationScenario, string, string]> = [
  ['publication-start', 'Начало публикации', 'Экран перед выбором бесплатного периода или тарифа'],
  ['founder-offer', 'Первые 20 клиентов', '14 дней бесплатно, без карты и автосписаний'],
  ['intro-no-renew', '500 ₽ без продления', '15 дней, после которых доступ просто заканчивается'],
  ['intro-auto-renew', '500 ₽ с продлением', 'Отдельное согласие на 1 500 ₽, затем 2 000 ₽'],
  ['all-plans', 'Все тарифы', '15, 30 и 90 дней на одном экране'],
  ['payment-pending', 'Ожидание оплаты', 'Состояние после перехода в ЮKassa'],
  ['founder-active', 'Бесплатный период активен', 'Founder-доступ и готовность к публикации'],
  ['subscription-active', 'Тариф активен', 'Срок, следующее списание и отключение продления'],
  ['published', 'Виджет опубликован', 'Готовый код установки для сайта'],
  ['feedback', 'Обратная связь', 'Оценка пилота и сообщение основателю'],
  ['tariff-limits', 'Лимит доработок', 'Состояние после расходования токенов'],
];

function LaunchSteps({ active, published = false }: { active: boolean; published?: boolean }) {
  const steps = [
    ['Доступ', active ? 'Подключён и готов к работе' : 'Founder-пилот или подходящий тариф', active ? 'completed' : 'current'],
    ['Публикация', published ? 'Выбранная версия уже доступна' : 'Вы сами выбираете момент запуска', published ? 'completed' : active ? 'current' : 'upcoming'],
    ['Установка', published ? 'Осталось добавить код на сайт' : 'Код появится после публикации', published ? 'current' : 'upcoming'],
  ] as const;
  return (
    <ol className="studio-upgrade__launch-steps" aria-label="Путь до запуска виджета">
      {steps.map(([title, copy, state], index) => (
        <li key={title} data-state={state} aria-current={state === 'current' ? 'step' : undefined}>
          <span aria-hidden>{state === 'completed' ? <CheckCircle size={16} weight="fill" /> : index + 1}</span>
          <div><strong>{title}</strong><small>{copy}</small></div>
        </li>
      ))}
    </ol>
  );
}

function UpgradeCard({ mode }: { mode: 'start' | 'pending' | 'founder' | 'paid' | 'published' | 'limits' }) {
  const active = !['start', 'pending'].includes(mode);
  const published = mode === 'published';
  const founder = mode === 'founder';
  const limits = mode === 'limits';
  return (
    <aside className="studio-upgrade presentation-studio__upgrade" aria-labelledby="presentation-upgrade-title">
      {active ? <CheckCircle aria-hidden size={22} weight="fill" /> : <Sparkle aria-hidden size={22} weight="fill" />}
      <div>
        <h2 id="presentation-upgrade-title">{published ? 'Виджет опубликован' : active ? 'Всё готово к публикации' : 'Подключите виджет к сайту'}</h2>
        <p>{published
          ? 'Виджет опубликован. Скопируйте код установки или постоянную ссылку ниже.'
          : active
            ? 'Ничего настраивать не нужно: сайт проекта будет разрешён автоматически. После публикации появится готовый код.'
            : 'Первая версия сохранена. Выберите бесплатный founder-пилот или подходящий тариф — условия будут показаны до перехода к оплате.'}</p>
        <LaunchSteps active={active} published={published} />
        {!active && <p className="studio-upgrade__safety-note">Founder-пилот не требует карты. Автопродление платного варианта включается только после отдельного согласия.</p>}
        {mode === 'pending' && <p className="studio-upgrade__status" role="status"><Clock aria-hidden size={17} /> Ожидаем подтверждение оплаты…</p>}
        {active && (
          <div className="studio-upgrade__publication">
            <p className="studio-upgrade__renewal-status">{founder
              ? 'Бесплатный период действует 14 дней. Автопродление выключено.'
              : 'Следующее списание — 1 500 ₽ 29 августа 2026 года. Автопродление включено.'}</p>
            <p className="studio-upgrade__renewal-status">{limits ? 'Лимит доработок на тарифе исчерпан.' : 'Доработки доступны в рамках тарифа.'}</p>
            {published && (
              <>
                <p role="status">Виджет опубликован и доступен на разрешённых сайтах.</p>
                <section className="studio-upgrade__handoff" aria-labelledby="presentation-handoff-title">
                  <span>Остался один шаг</span>
                  <h3 id="presentation-handoff-title">Установите виджет на сайт</h3>
                  <p>Скопируйте код установки или передайте код человеку, который управляет сайтом. Последующие обновления будут приходить по тому же адресу.</p>
                  <div className="studio-upgrade__handoff-actions">
                    <button type="button"><Copy aria-hidden size={18} weight="bold" /> Скопировать код установки</button>
                    <button type="button"><Copy aria-hidden size={18} weight="bold" /> Скопировать ссылку загрузчика</button>
                  </div>
                  <a className="studio-upgrade__install-guide" href="/install">
                    Открыть инструкцию по установке
                  </a>
                </section>
                <details className="studio-upgrade__developer" open>
                  <summary>Код для разработчика</summary>
                  <div><code>{'<script src="https://kaigo.space/embed/demo.js" async></script>'}</code></div>
                </details>
              </>
            )}
          </div>
        )}
      </div>
      {!active ? (
        <button type="button">{mode === 'pending' ? <Clock aria-hidden size={18} /> : <ArrowRight aria-hidden size={18} />}{mode === 'pending' ? 'Проверяем оплату' : 'Выбрать условия публикации'}</button>
      ) : (
        <div className="studio-upgrade__actions">
          <button type="button"><ArrowRight aria-hidden size={18} />{published ? 'Обновить публикацию' : 'Опубликовать и получить код'}</button>
          {!founder && <button type="button">Отключить автопродление</button>}
          <button type="button">Связаться с Kaigo</button>
        </div>
      )}
    </aside>
  );
}

function PresentationShell({ children }: { children: React.ReactNode }) {
  return (
    <main className="presentation-studio">
      <header className="presentation-studio__header">
        <KaigoLogo tone="coral" />
        <span>ДЕМО-РЕЖИМ · ИЗМЕНЕНИЯ НЕ СОХРАНЯЮТСЯ</span>
        <a href="/studio">Вернуться в студию</a>
      </header>
      <section className="presentation-studio__canvas">{children}</section>
    </main>
  );
}

export function StudioPresentation({ scenario }: { scenario: PresentationScenario }) {
  if (scenario === 'index') {
    return (
      <PresentationShell>
        <div className="presentation-index">
          <span>KAIGO · СЪЁМКА ПРОДУКТА</span>
          <h1>Экраны для ролика</h1>
          <p>Каждая ссылка открывает готовое состояние интерфейса. Никакие платежи, подписки и публикации не создаются.</p>
          <div>{LINKS.map(([id, title, copy]) => (
            <a key={id} href={`/studio?presentation=${id}`}><strong>{title}</strong><small>{copy}</small><ArrowRight aria-hidden /></a>
          ))}</div>
        </div>
      </PresentationShell>
    );
  }

  if (['founder-offer', 'intro-no-renew', 'intro-auto-renew', 'all-plans'].includes(scenario)) {
    const founder = scenario === 'founder-offer';
    const plans = scenario.startsWith('intro-') ? [INTRO_PLAN] : ALL_PLANS;
    return (
      <PresentationShell>
        <UpgradeCard mode="start" />
        <PublicationOfferDialog
          open
          offer={offer(founder, plans)}
          busy={false}
          error={null}
          initialIntroConsent={scenario === 'intro-auto-renew'}
          onClose={() => undefined}
          onFounder={() => undefined}
          onCheckout={() => undefined}
        />
      </PresentationShell>
    );
  }

  if (scenario === 'feedback') {
    return (
      <PresentationShell>
        <UpgradeCard mode="founder" />
        <SupportDialog open founder busy={false} error={null} sent={false} onClose={() => undefined} onSubmit={() => undefined} />
      </PresentationShell>
    );
  }

  const modes = {
    'publication-start': 'start',
    'payment-pending': 'pending',
    'founder-active': 'founder',
    'subscription-active': 'paid',
    published: 'published',
    'tariff-limits': 'limits',
  } satisfies Partial<Record<PresentationScenario, Parameters<typeof UpgradeCard>[0]['mode']>>;
  const mode = modes[scenario as keyof typeof modes];
  return mode ? <PresentationShell><UpgradeCard mode={mode} /></PresentationShell> : null;
}
