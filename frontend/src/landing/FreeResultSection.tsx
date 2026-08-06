import { ChatCircleDots, ClockCountdown, CreditCard, SealCheck } from '@phosphor-icons/react';

const facts = [
  {
    Icon: SealCheck,
    title: 'Экспресс-версия бесплатно',
    copy: 'Бесплатная экспресс-версия — одна на подтверждённый аккаунт после входа через Google или Яндекс.',
  },
  {
    Icon: ChatCircleDots,
    title: 'Проверьте виджет в чате',
    copy: 'Откройте предпросмотр и задайте вопросы до решения об оплате.',
  },
  {
    Icon: CreditCard,
    title: 'Оплата только перед запуском',
    copy: 'Карта не нужна для генерации. Тариф понадобится, когда решите опубликовать.',
  },
] as const;

export function FreeResultSection() {
  return (
    <section className="landing-section free-result-section" data-landing-section data-surface="metal">
      <div className="landing-shell free-result-section__inner">
        <div className="free-result-section__copy">
          <p className="section-kicker">Честный бесплатный старт</p>
          <h2>Сначала посмотрите результат. Оплатите только публикацию.</h2>
          <div className="free-result-section__timing">
            <ClockCountdown size={27} aria-hidden />
            <span><strong>Обычно 10–20 минут, сложные сайты дольше</strong><small>Точное время зависит от количества страниц</small></span>
          </div>
        </div>
        <ol className="free-result-section__facts" aria-label="Что входит в бесплатный старт">
          {facts.map(({ Icon, title, copy }, index) => (
            <li key={title}>
              <span className="free-result-section__number" aria-hidden>{String(index + 1).padStart(2, '0')}</span>
              <Icon size={26} aria-hidden />
              <div><h3>{title}</h3><p>{copy}</p></div>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
