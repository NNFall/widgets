import { ChatsCircle, LinkSimple, Scan } from '@phosphor-icons/react';
import { motion } from 'motion/react';

import { BrowserMockup } from '../shared/BrowserMockup';
import { useHeroMotionCycle } from './useHeroMotionCycle';

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

export function HeroOrbitScene() {
  const { program, phase, cycle, visibleCards, reducedMotion } = useHeroMotionCycle();
  const cardsVisible = visibleCards > 0;
  const widgetVisible = phase === 'widget' || phase === 'complete';
  const motionComplete = phase === 'complete';

  return (
    <div
      className="hero-scene"
      data-testid="hero-scene"
      data-motion-program={program}
      data-motion-phase={phase}
      data-motion-cycle={cycle}
      data-visible-cards={visibleCards}
      aria-describedby="hero-scene-description"
    >
      <p className="sr-only" id="hero-scene-description">
        Анимация показывает, как Kaigo анализирует исходный сайт и добавляет готовый AI-виджет.
      </p>
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
            data-visible={index < visibleCards ? 'true' : 'false'}
            key={number}
            initial={false}
            animate={{
              opacity: index < visibleCards ? 1 : 0,
              y: motionComplete && !reducedMotion ? [0, -4, 0] : index < visibleCards ? 0 : 18,
              scale: index < visibleCards ? 1 : 0.94,
            }}
            transition={
              motionComplete && !reducedMotion
                ? { duration: 5.2 + index * 0.35, repeat: Infinity, ease: 'easeInOut' }
                : { type: 'spring', stiffness: 100, damping: 18, delay: index < visibleCards ? index * 0.3 : 0 }
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
        <div className="hero-browser-stage__scanner-markup" aria-hidden="true">
          <span className="hero-browser-stage__scanner-label" hidden={phase !== 'scanning'}>
            Сканирование…
          </span>
          <motion.div
            className="hero-browser-stage__scanner"
            initial={false}
            animate={
              phase === 'scanning'
                ? { opacity: [0, 1, 1, 0], y: ['0%', '520%'] }
                : { opacity: 0, y: '0%' }
            }
            transition={{ duration: phase === 'scanning' ? 2.6 : 0.15, ease: 'easeInOut' }}
          />
        </div>
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
