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

const steps = [
  {
    number: '1',
    title: 'Добавьте сайт',
    copy: 'Укажите одну публичную ссылку. Анкета и технические настройки пока не нужны.',
    Icon: LinkSimple,
  },
  {
    number: '2',
    title: 'Дождитесь анализа',
    copy: 'Kaigo сам откроет страницы и покажет, из чего собирает будущего консультанта.',
    Icon: MagnifyingGlass,
  },
  {
    number: '3',
    title: 'Проверьте AI-виджет',
    copy: 'Откройте предпросмотр, задайте вопросы в чате и публикуйте только после проверки.',
    Icon: ChatsCircle,
  },
] as const;

const analysisFacts = ['12 страниц открыто', '8 услуг найдено', 'Тон общения определён'] as const;

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
          <p>Один понятный путь: вы даёте ссылку, Kaigo собирает контекст, а вы проверяете готовый разговор.</p>
        </Reveal>

        <div className="how-process-board" data-journey-layout="unified">
          <div className="how-process-board__meta">
            <span>Бесплатная часть</span>
            <strong>3 этапа, обычно 10–20 минут</strong>
          </div>
          <ol className="how-grid" aria-label="Путь от ссылки до готового виджета">
            {steps.map(({ number, title, copy, Icon }, index) => (
              <li className={`how-step how-step--${index + 1}`} key={number}>
                <span className="step-number">{number}</span>
                <Icon size={30} weight="regular" aria-hidden="true" />
                <span><strong>{title}</strong><small>{copy}</small></span>
              </li>
            ))}
          </ol>

          <Reveal className="how-live-preview" data-testid="how-live-preview" preset="scale">
            <div className="how-stage how-stage--site">
              <span className="how-stage__label">1 · Ваш сайт</span>
              <div className="how-stage__address"><LinkSimple size={22} /><strong>https://teply-hleb.ru</strong><CheckCircle size={24} weight="fill" /></div>
              <p>Ссылка принята. Никаких анкет и технических настроек.</p>
            </div>
            <ArrowRight className="how-stage__arrow" size={30} weight="bold" aria-hidden="true" />
            <div className="how-stage how-stage--analysis">
              <span className="how-stage__label">2 · Анализ</span>
              <strong>Kaigo собирает контекст</strong>
              <p>Проверяем страницы, услуги, стиль и частые вопросы</p>
              <div className="how-stage__scan-track" aria-hidden="true"><i className="how-stage__scan-line" /></div>
              <ul>
                {analysisFacts.map((fact) => <li key={fact}><CheckCircle size={18} weight="fill" />{fact}</li>)}
              </ul>
            </div>
            <ArrowRight className="how-stage__arrow" size={30} weight="bold" aria-hidden="true" />
            <div className="how-stage how-stage--chat">
              <div className="how-stage__chat-head"><span className="how-stage__label">3 · AI-консультант</span><small><i className="how-stage__status-dot" />На связи</small></div>
              <p className="how-stage__question">Какие торты можно заказать к субботе?</p>
              <p className="how-stage__answer">Есть четыре начинки. Подскажите число гостей, и я помогу выбрать размер.</p>
              <div className="how-stage__input"><span>Введите вопрос</span><ArrowRight size={17} weight="bold" /></div>
            </div>
          </Reveal>

          <Reveal
            className="how-launch"
            data-launch-integrated="true"
            data-testid="how-publish-path"
            preset="heading"
          >
            <div className="how-launch__copy">
              <p className="section-kicker">После бесплатной проверки</p>
              <h3>Публикуйте только после проверки</h3>
              <p>Виджет не появится на сайте без вашего подтверждения.</p>
            </div>
            <ol aria-label="Шаги после проверки">
              {launchSteps.map(({ title, copy, Icon }, index) => (
                <li key={title}>
                  <span aria-hidden><Icon size={20} /></span>
                  <div><strong>{title}</strong><small>{copy}</small></div>
                  <i aria-hidden>{index + 1}</i>
                </li>
              ))}
            </ol>
            <div className="how-launch__action" data-testid="how-publish-action">
              <a className="primary-button" href={campaignStudioHref}>Создать бесплатную версию</a>
              <small>Без карты. Оплата только перед публикацией.</small>
            </div>
          </Reveal>
        </div>
      </div>
    </section>
  );
}
