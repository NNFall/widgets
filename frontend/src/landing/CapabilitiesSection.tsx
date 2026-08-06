import { ArrowRight, BookOpen, Clock, EnvelopeSimple, Eye, Target, Textbox } from '@phosphor-icons/react';
import { useEffect, useState } from 'react';

import { KaigoLogo } from '../shared/KaigoLogo';
import { useMotionActivity } from '../shared/MotionActivity';
import { Reveal } from '../shared/Reveal';

export const CAPABILITY_CARD_STAGGER_SECONDS = 0.26;
export const CAPABILITY_CHAT_REVEAL_DELAY_SECONDS = 0;
export const CAPABILITY_SATELLITE_REVEAL_BASE_SECONDS = 0.34;
export const CAPABILITY_REVEAL_DELAYS_SECONDS = Array.from(
  { length: 6 },
  (_, order) => Number((order * CAPABILITY_CARD_STAGGER_SECONDS).toFixed(2)),
);

export type CapabilityConversationPhase = 'suggestion' | 'user' | 'typing' | 'answer';

const CONVERSATION_PHASES: Record<CapabilityConversationPhase, {
  durationMs: number;
  next: CapabilityConversationPhase;
}> = {
  suggestion: { durationMs: 1800, next: 'user' },
  user: { durationMs: 1300, next: 'typing' },
  typing: { durationMs: 1600, next: 'answer' },
  answer: { durationMs: 5200, next: 'suggestion' },
};

export function useCapabilityConversationCycle(
  active: boolean,
  reducedMotion: boolean,
): CapabilityConversationPhase {
  const [phase, setPhase] = useState<CapabilityConversationPhase>('suggestion');

  useEffect(() => {
    if (!active || reducedMotion) return undefined;

    const current = CONVERSATION_PHASES[phase];
    const timeout = window.setTimeout(() => setPhase(current.next), current.durationMs);
    return () => window.clearTimeout(timeout);
  }, [active, phase, reducedMotion]);

  return reducedMotion ? 'answer' : phase;
}

const capabilities = [
  { title: 'Знает ваш бизнес', copy: 'Опирается на страницы, услуги и заданные инструкции.', Icon: BookOpen, revealOrder: 0 },
  { title: 'Говорит в стиле бренда', copy: 'Сохраняет тон общения и визуальный характер сайта.', Icon: Textbox, revealOrder: 1 },
  { title: 'Отвечает 24/7', copy: 'Не заставляет посетителя ждать рабочего дня.', Icon: Clock, revealOrder: 2 },
  { title: 'Помогает выбрать', copy: 'Уточняет задачу и предлагает подходящий следующий шаг.', Icon: Target, revealOrder: 3 },
  { title: 'Собирает обращения', copy: 'Передаёт контакты в Telegram или на почту.', Icon: EnvelopeSimple, revealOrder: 4 },
  { title: 'Можно проверить', copy: 'Предпросмотр и чат помогают оценить готовый виджет до публикации.', Icon: Eye, revealOrder: 5 },
] as const;

export function CapabilitiesSection() {
  const { active, reducedMotion, ref } = useMotionActivity<HTMLElement>();
  const conversationPhase = useCapabilityConversationCycle(active, reducedMotion);

  const renderCapability = ({ title, copy, Icon, revealOrder }: (typeof capabilities)[number]) => (
    <Reveal
      className="capability-item"
      delay={CAPABILITY_SATELLITE_REVEAL_BASE_SECONDS + CAPABILITY_REVEAL_DELAYS_SECONDS[revealOrder]}
      preset={revealOrder % 2 === 0 ? 'fromLeft' : 'fromRight'}
      key={title}
    >
      <span className="capability-item__icon" data-capability-order={revealOrder} aria-hidden="true">
        <Icon size={39} weight="regular" />
      </span>
      <span><strong>{title}</strong><small>{copy}</small></span>
    </Reveal>
  );

  return (
    <section
      className="landing-section capabilities-section"
      id="capabilities"
      data-landing-section
      data-motion-active={active ? 'true' : 'false'}
      ref={ref}
    >
      <div className="landing-shell">
        <Reveal className="section-heading">
          <p className="section-kicker">AI-сотрудник на вашем сайте</p>
          <h2>Не просто чат.<br />AI-сотрудник на вашем сайте</h2>
          <p>Виджет получает знания о бизнесе, общается в нужном тоне и помогает посетителю перейти от вопроса к действию.</p>
        </Reveal>

        <div className="capability-stage">
          <div className="capability-column capability-column--left">
            {capabilities.slice(0, 3).map(renderCapability)}
          </div>

          <Reveal className="chat-showcase-reveal" delay={CAPABILITY_CHAT_REVEAL_DELAY_SECONDS} preset="scale">
            <div className="chat-showcase">
              <div className="chat-showcase__chrome"><span /><span /><span /></div>
              <div className="chat-showcase__head"><KaigoLogo className="kaigo-logo--compact" tone="coral" /><span><strong>Kaigo AI</strong><small>Онлайн</small></span></div>
              <div className="chat-showcase__body" data-chat-phase={conversationPhase}>
                <div className="chat-showcase__message"><KaigoLogo className="kaigo-logo--compact" tone="coral" /><p>Расскажите, что вы ищете.<br />Я помогу сориентироваться.</p></div>
                <div className="chat-showcase__suggestions">
                  <span className={conversationPhase === 'suggestion' ? 'is-selected' : ''}>Какие торты можно заказать к субботе?</span>
                  <span>Есть доставка по городу?</span>
                  <span>Что можно забрать сегодня?</span>
                </div>
                <div className="capability-chat__turns" aria-label="Пример диалога с AI-сотрудником">
                  <p className="capability-chat__user">Какие торты можно заказать к субботе?</p>
                  <div className="capability-chat__typing" aria-hidden="true"><span /><span /><span /></div>
                  <div className="capability-chat__answer">
                    <KaigoLogo className="kaigo-logo--compact" tone="coral" />
                    <p>К субботе доступны четыре начинки. Уточните число гостей, и я подскажу размер и срок заказа.</p>
                  </div>
                </div>
                <div className="chat-showcase__input"><span>Введите сообщение...</span><ArrowRight size={19} weight="bold" /></div>
                <small>AI-сотрудник может ошибаться. Проверяйте важное.</small>
              </div>
            </div>
          </Reveal>

          <div className="capability-column capability-column--right">
            {capabilities.slice(3).map(renderCapability)}
          </div>
        </div>
      </div>
    </section>
  );
}
