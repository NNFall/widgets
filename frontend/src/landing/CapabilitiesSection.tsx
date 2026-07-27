import { ArrowRight, BookOpen, Clock, EnvelopeSimple, SlidersHorizontal, Target, Textbox } from '@phosphor-icons/react';

import { KaigoLogo } from '../shared/KaigoLogo';
import { Reveal } from '../shared/Reveal';

export const CAPABILITY_CARD_STAGGER_SECONDS = 0.26;

const capabilities = [
  { title: 'Знает ваш бизнес', copy: 'Опирается на страницы, услуги и заданные инструкции.', Icon: BookOpen },
  { title: 'Говорит в стиле бренда', copy: 'Сохраняет тон общения и визуальный характер сайта.', Icon: Textbox },
  { title: 'Отвечает 24/7', copy: 'Не заставляет посетителя ждать рабочего дня.', Icon: Clock },
  { title: 'Помогает выбрать', copy: 'Уточняет задачу и предлагает подходящий следующий шаг.', Icon: Target },
  { title: 'Собирает обращения', copy: 'Передаёт контакты в Telegram или на почту.', Icon: EnvelopeSimple },
  { title: 'Можно дорабатывать', copy: 'Поведение и текст меняются до публикации.', Icon: SlidersHorizontal },
] as const;

export function CapabilitiesSection() {
  return (
    <section className="landing-section capabilities-section" id="capabilities" data-landing-section>
      <div className="landing-shell">
        <Reveal className="section-heading">
          <p className="section-kicker">AI-сотрудник на вашем сайте</p>
          <h2>Не просто чат.<br />AI-сотрудник на вашем сайте</h2>
          <p>Виджет получает знания о бизнесе, общается в нужном тоне и помогает посетителю перейти от вопроса к действию.</p>
        </Reveal>

        <div className="capability-stage">
          <div className="capability-column capability-column--left">
            {capabilities.slice(0, 3).map(({ title, copy, Icon }, index) => (
              <Reveal className="capability-item" delay={index * CAPABILITY_CARD_STAGGER_SECONDS} key={title}>
                <Icon size={39} weight="regular" aria-hidden="true" /><span><strong>{title}</strong><small>{copy}</small></span>
              </Reveal>
            ))}
          </div>

          <Reveal className="chat-showcase" delay={0.1}>
            <div className="chat-showcase__chrome"><span /><span /><span /></div>
            <div className="chat-showcase__head"><KaigoLogo className="kaigo-logo--compact" /><span><strong>Kaigo AI</strong><small>Онлайн</small></span></div>
            <div className="chat-showcase__body">
              <div className="chat-showcase__message"><KaigoLogo className="kaigo-logo--compact" /><p>Расскажите, что вы ищете —<br />я помогу сориентироваться.</p></div>
              <div className="chat-showcase__suggestions">
                <button type="button">Хочу консультацию по проекту</button>
                <button type="button">Сколько стоит строительство?</button>
                <button type="button">Посмотреть реализованные проекты</button>
              </div>
              <div className="chat-showcase__input"><span>Введите сообщение...</span><ArrowRight size={19} weight="bold" /></div>
              <small>AI-сотрудник может ошибаться. Проверяйте важное.</small>
            </div>
          </Reveal>

          <div className="capability-column capability-column--right">
            {capabilities.slice(3).map(({ title, copy, Icon }, index) => (
              <Reveal className="capability-item" delay={index * CAPABILITY_CARD_STAGGER_SECONDS} key={title}>
                <Icon size={39} weight="regular" aria-hidden="true" /><span><strong>{title}</strong><small>{copy}</small></span>
              </Reveal>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
