import { ChatCircleDots, CreditCard, SealCheck } from '@phosphor-icons/react';

const facts = [
  {
    Icon: SealCheck,
    title: 'Одна готовая экспресс-версия бесплатно',
    copy: 'После входа через Google или Яндекс — одна на подтверждённый аккаунт.',
  },
  {
    Icon: CreditCard,
    title: 'Без карты',
    copy: 'Платите только за публикацию и подключение готового виджета.',
  },
  {
    Icon: ChatCircleDots,
    title: 'Сначала предпросмотр и чат',
    copy: 'Проверьте результат до любого решения об оплате.',
  },
] as const;

export function FreeResultSection() {
  return (
    <section className="landing-section free-result-section" data-landing-section>
      <div className="landing-shell free-result-section__inner">
        <div className="free-result-section__copy">
          <p className="section-kicker">Честный бесплатный старт</p>
          <h2>Сначала результат — потом оплата</h2>
          <p>Обычно 10–20 минут, сложные сайты дольше.</p>
        </div>
        <div className="free-result-section__facts">
          {facts.map(({ Icon, title, copy }) => (
            <article key={title}>
              <Icon size={26} aria-hidden />
              <h3>{title}</h3>
              <p>{copy}</p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
