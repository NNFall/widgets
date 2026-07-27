import { ChatsCircle, LinkSimple, Scan } from '@phosphor-icons/react';
import { motion, type Variants } from 'motion/react';

import { BrowserMockup } from '../shared/BrowserMockup';
import { useHeroMotionCycle } from './useHeroMotionCycle';

const processCards = [
  {
    number: '1',
    direction: 'upper-left',
    title: 'Ссылка на сайт',
    copy: 'Запуск без анкеты и кода',
    Icon: LinkSimple,
  },
  {
    number: '2',
    direction: 'left',
    title: 'Визуальный анализ',
    copy: 'Контент, услуги, стиль и вопросы',
    Icon: Scan,
  },
  {
    number: '3',
    direction: 'lower-left',
    title: 'AI-консультант',
    copy: 'Готов к проверке и доработке',
    Icon: ChatsCircle,
  },
] as const;

const processCardVariants: Variants = {
  hidden: {
    opacity: 0,
    x: 'var(--card-hidden-x)',
    y: 'var(--card-hidden-y)',
    scale: 0.82,
    rotate: 'var(--card-hidden-rotate)',
  },
  visible: {
    opacity: [0, 1, 1],
    x: ['var(--card-hidden-x)', 'var(--card-overshoot-x)', '0px'],
    y: ['var(--card-hidden-y)', 'var(--card-overshoot-y)', '0px'],
    scale: [0.82, 1.04, 1],
    rotate: ['var(--card-hidden-rotate)', 'var(--card-overshoot-rotate)', '0deg'],
  },
  settled: {
    opacity: 1,
    x: '0px',
    y: '0px',
    scale: 1,
    rotate: '0deg',
  },
  rest: {
    opacity: 1,
    x: ['0px', 'var(--card-drift-x)', '0px'],
    y: ['0px', 'var(--card-drift-y)', '0px'],
    scale: 1,
    rotate: ['0deg', 'var(--card-drift-rotate)', '0deg'],
  },
};

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
        {processCards.map(({ number, direction, title, copy, Icon }, index) => (
          <motion.article
            className={`process-card process-card--${index + 1}`}
            data-testid="process-card"
            data-visible={index < visibleCards ? 'true' : 'false'}
            data-reveal-direction={direction}
            data-resting={
              motionComplete && !reducedMotion && index === cycle % processCards.length
                ? 'true'
                : 'false'
            }
            key={number}
            initial={false}
            animate={
              motionComplete && !reducedMotion
                ? index === cycle % processCards.length ? 'rest' : 'settled'
                : index < visibleCards ? 'visible' : 'hidden'
            }
            variants={processCardVariants}
            transition={
              reducedMotion
                ? { duration: 0, delay: 0 }
                : motionComplete && index === cycle % processCards.length
                  ? {
                    duration: 5.4 + index * 0.45,
                    repeat: Infinity,
                    repeatDelay: 0.8 + index * 0.35,
                    ease: 'easeInOut',
                  }
                  : motionComplete
                    ? { duration: 0.3, ease: 'easeOut' }
                    : index < visibleCards
                      ? {
                        duration: 0.78,
                        times: [0, 0.7, 1],
                        ease: [0.16, 1, 0.3, 1],
                      }
                      : { duration: 0.34, ease: 'easeOut' }
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
          variant="hero"
        />
        <div
          className="hero-browser-stage__scanner-markup"
          aria-hidden="true"
        >
          <div
            className="hero-browser-stage__scanner"
            data-testid="hero-scanner"
            data-active={phase === 'scanning' ? 'true' : 'false'}
            aria-hidden="true"
          >
            <span className="hero-browser-stage__scanner-label" hidden={phase !== 'scanning'}>
              Сканирование…
            </span>
            <span
              className="hero-browser-stage__scanner-band"
              data-testid="hero-scanner-band"
            />
            <span
              className="hero-browser-stage__scanner-core"
              data-testid="hero-scanner-core"
            />
            <span
              className="hero-browser-stage__scanner-trail"
              data-testid="hero-scanner-trail"
            />
            {[0, 1, 2].map((particle) => (
              <span
                className={`hero-browser-stage__scanner-particle hero-browser-stage__scanner-particle--${particle + 1}`}
                data-testid="hero-scanner-particle"
                key={particle}
              />
            ))}
          </div>
        </div>
        <motion.div
          className="hero-browser-stage__widget-label"
          initial={false}
          animate={{ opacity: widgetVisible ? 1 : 0, y: widgetVisible ? 0 : 12 }}
          transition={{
            duration: reducedMotion ? 0 : 0.55,
            delay: reducedMotion ? 0 : widgetVisible ? 0.25 : 0,
          }}
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
