import {
  ArrowRight,
  CheckCircle,
  ChatsCircle,
  CloudArrowUp,
  Code,
  CreditCard,
  LinkSimple,
  MagnifyingGlass,
} from '@phosphor-icons/react';

import { Reveal } from '../shared/Reveal';
import { useMotionActivity } from '../shared/MotionActivity';
import { studioHref } from '../shared/campaign';

export const HOW_CARD_STAGGER_SECONDS = 0.18;

const steps = [
  {
    number: '1',
    title: 'Добавьте сайт',
    copy: 'Укажите одну публичную ссылку. Анкета и технические настройки пока не нужны.',
    Icon: LinkSimple,
    artifact: (
      <div className="process-artifact process-artifact--url">
        <span>Ссылка на сайт</span>
        <div><span>https://teply-hleb.ru</span><CheckCircle className="how-confirmation-pulse" size={25} weight="fill" /></div>
        <small>Подойдёт сайт, лендинг или страница услуг</small>
      </div>
    ),
  },
  {
    number: '2',
    title: 'Дождитесь анализа',
    copy: 'Kaigo сам откроет страницы и покажет, из чего собирает будущего консультанта.',
    Icon: MagnifyingGlass,
    artifact: (
      <div className="process-artifact process-artifact--scan">
        <strong>Проверяем страницы, услуги, стиль и частые вопросы</strong>
        <i className="how-checklist-progress" aria-hidden="true" />
        {['12 страниц открыто', '8 услуг найдено', 'Тон общения определён'].map((item, index) => (
          <span className={index < 2 ? 'is-active' : ''} key={item}>
            <i>{index < 2 ? <CheckCircle size={17} weight={index === 0 ? 'fill' : 'regular'} /> : null}</i>{item}
          </span>
        ))}
        <small className="process-artifact__result"><CheckCircle size={15} weight="fill" /> Контекст готов</small>
      </div>
    ),
  },
  {
    number: '3',
    title: 'Проверьте AI-виджет',
    copy: 'Откройте предпросмотр, задайте вопросы в чате и публикуйте только после проверки.',
    Icon: ChatsCircle,
    artifact: (
      <div className="process-artifact process-artifact--chat">
        <div><strong>AI-консультант</strong><span><i /> Готов помочь</span></div>
        <p className="is-user">Какие торты можно заказать к субботе?</p>
        <div className="how-chat-response-window">
          <div className="how-chat-response">
            <span className="how-chat-typing" aria-hidden="true"><i /><i /><i /></span>
            <p>Есть четыре начинки. Подскажите число гостей, и я помогу выбрать размер.</p>
          </div>
        </div>
        <div className="mini-input"><span>Задайте вопрос...</span><ArrowRight size={16} weight="bold" /></div>
      </div>
    ),
  },
] as const;

const launchSteps = [
  {
    title: 'Выберите тариф',
    copy: 'Он открывает доработки и публикацию.',
    Icon: CreditCard,
  },
  {
    title: 'Опубликуйте версию',
    copy: 'Вы сами выбираете готовый вариант и сайты.',
    Icon: CloudArrowUp,
  },
  {
    title: 'Установите одной строкой',
    copy: 'Скопируйте код или передайте его разработчику.',
    Icon: Code,
  },
] as const;

export function HowItWorksSection() {
  const campaignStudioHref = studioHref();
  const { active, ref } = useMotionActivity<HTMLElement>();

  return (
    <section
      className="landing-section how-section"
      id="how-it-works"
      data-landing-section
      data-motion-active={active ? 'true' : 'false'}
      ref={ref}
    >
      <div className="landing-shell">
        <Reveal className="section-heading section-heading--wide" preset="heading">
          <p className="section-kicker">От ссылки до результата</p>
          <h2>Как это работает</h2>
          <p>Три понятных действия. На каждом этапе видно, что происходит и что потребуется от вас.</p>
        </Reveal>

        <div className="how-process-board">
          <svg className="how-route" viewBox="0 0 1200 130" aria-hidden="true">
            <path className="how-route__path" d="M205 63 C315 4 350 118 460 66 S667 12 760 68 S966 115 1062 58" />
          </svg>
          <ol className="how-grid" aria-label="Путь от ссылки до готового виджета">
            {steps.map(({ number, title, copy, Icon, artifact }, index) => (
              <li className={`how-step how-step--${index + 1}`} key={number}>
                <Reveal
                  className="how-card__entrance"
                  delay={index * HOW_CARD_STAGGER_SECONDS}
                  preset={index % 2 === 0 ? 'fromLeft' : 'fromRight'}
                >
                  <article className={`how-card how-card--${index + 1}`}>
                    <div className="how-card__header">
                      <span className="step-number">{number}</span>
                      <Icon size={43} weight="regular" aria-hidden="true" />
                      <h3>{title}</h3>
                    </div>
                    <p>{copy}</p>
                    {artifact}
                  </article>
                </Reveal>
              </li>
            ))}
          </ol>
        </div>
        <Reveal className="how-launch" preset="heading">
          <div className="how-launch__copy">
            <p className="section-kicker">После бесплатной проверки</p>
            <h3>Дальше: три шага до запуска на сайте</h3>
            <p>Виджет не появится на сайте без вашего подтверждения. Сначала вы принимаете результат, затем управляете запуском.</p>
          </div>
          <ol aria-label="Шаги после проверки">
            {launchSteps.map(({ title, copy, Icon }, index) => (
              <li key={title}>
                <span aria-hidden><Icon size={21} /></span>
                <div><strong>{title}</strong><small>{copy}</small></div>
                <i aria-hidden>{index + 1}</i>
              </li>
            ))}
          </ol>
        </Reveal>
        <Reveal className="section-action"><a className="primary-button" href={campaignStudioHref}>Создать бесплатную версию</a></Reveal>
      </div>
    </section>
  );
}
