import { ChatsCircle, LinkSimple, Scan } from '@phosphor-icons/react';
import { motion } from 'motion/react';
import { useEffect, useState } from 'react';

import { BrowserMockup } from '../shared/BrowserMockup';

type MotionPhase = 'source' | 'scanning' | 'transforming' | 'cards' | 'widget' | 'complete';

const phaseTimeline: Array<[MotionPhase, number]> = [
  ['scanning', 1_800],
  ['transforming', 4_400],
  ['cards', 4_900],
  ['widget', 6_000],
  ['complete', 7_400],
];

const phaseOrder: Record<MotionPhase, number> = {
  source: 0,
  scanning: 1,
  transforming: 2,
  cards: 3,
  widget: 4,
  complete: 5,
};

const processCards = [
  {
    number: '1',
    title: 'Ссылка на сайт',
    copy: 'Запуск без анкеты и кода',
    Icon: LinkSimple,
  },
  {
    number: '2',
    title: 'Визуальный анализ',
    copy: 'Контент, услуги, стиль и вопросы',
    Icon: Scan,
  },
  {
    number: '3',
    title: 'AI-консультант',
    copy: 'Готов к проверке и доработке',
    Icon: ChatsCircle,
  },
] as const;

function reducedMotionRequested() {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export function HeroOrbitScene() {
  const [reducedMotion] = useState(reducedMotionRequested);
  const [phase, setPhase] = useState<MotionPhase>(() =>
    reducedMotionRequested() ? 'complete' : 'source',
  );

  useEffect(() => {
    if (reducedMotion) return;

    const timers = phaseTimeline.map(([nextPhase, delay]) =>
      window.setTimeout(() => setPhase(nextPhase), delay),
    );

    return () => timers.forEach(window.clearTimeout);
  }, [reducedMotion]);

  const cardsVisible = phaseOrder[phase] >= phaseOrder.cards;
  const widgetVisible = phaseOrder[phase] >= phaseOrder.widget;
  const motionComplete = phase === 'complete';

  return (
    <div
      className="hero-scene"
      data-testid="hero-scene"
      data-motion-phase={phase}
      aria-label="Сайт превращается в персональный AI-виджет"
    >
      <svg className="hero-scene__orbit" viewBox="0 0 360 610" aria-hidden="true">
        <motion.path
          d="M205 16 C70 74, 83 164, 225 188 C325 206, 302 288, 146 314 C38 335, 75 430, 218 449 C318 464, 289 551, 133 590"
          fill="none"
          stroke="#fe6936"
          strokeWidth="3"
          strokeLinecap="round"
          strokeDasharray="11 14"
          initial={false}
          animate={{ pathLength: cardsVisible ? 1 : 0, opacity: cardsVisible ? 0.92 : 0 }}
          transition={{ duration: reducedMotion ? 0 : 1.25, ease: 'easeInOut' }}
        />
      </svg>

      <div className="hero-scene__cards">
        {processCards.map(({ number, title, copy, Icon }, index) => (
          <motion.article
            className={`process-card process-card--${index + 1}`}
            data-testid="process-card"
            data-visible={cardsVisible ? 'true' : 'false'}
            key={number}
            initial={false}
            animate={{
              opacity: cardsVisible ? 1 : 0,
              y: motionComplete && !reducedMotion ? [0, -4, 0] : cardsVisible ? 0 : 18,
              scale: cardsVisible ? 1 : 0.94,
            }}
            transition={
              motionComplete && !reducedMotion
                ? { duration: 5.2 + index * 0.35, repeat: Infinity, ease: 'easeInOut' }
                : { type: 'spring', stiffness: 100, damping: 18, delay: cardsVisible ? index * 0.3 : 0 }
            }
          >
            <span className="process-card__number">{number}</span>
            <Icon className="process-card__icon" size={42} weight="regular" aria-hidden="true" />
            <span className="process-card__copy">
              <strong>{title}</strong>
              <span>{copy}</span>
            </span>
          </motion.article>
        ))}
      </div>

      <div className="hero-browser-stage">
        <span className="hero-browser-stage__source-label">исходный сайт</span>
        <BrowserMockup
          widgetVisible={widgetVisible}
          motionComplete={motionComplete}
          reducedMotion={reducedMotion}
        />
        <motion.div
          className="hero-browser-stage__scanner"
          initial={false}
          animate={
            phase === 'scanning'
              ? { opacity: [0, 1, 1, 0], y: ['0%', '520%'] }
              : { opacity: 0, y: '0%' }
          }
          transition={{ duration: phase === 'scanning' ? 2.6 : 0.15, ease: 'easeInOut' }}
          aria-hidden="true"
        />
        <motion.div
          className="hero-browser-stage__widget-label"
          initial={false}
          animate={{ opacity: widgetVisible ? 1 : 0, y: widgetVisible ? 0 : 12 }}
          transition={{ duration: reducedMotion ? 0 : 0.55, delay: reducedMotion ? 0 : 0.25 }}
        >
          Готовый AI-виджет
          <svg viewBox="0 0 76 54" aria-hidden="true">
            <path d="M4 48 C36 46, 59 29, 67 7" fill="none" stroke="currentColor" strokeWidth="2" />
            <path d="m58 12 10-6 2 12" fill="none" stroke="currentColor" strokeWidth="2" />
          </svg>
        </motion.div>
      </div>
    </div>
  );
}
