import { ArrowRight, FileText, Heart, ShieldCheck, Sparkle } from '@phosphor-icons/react';
import { motion, type Variants } from 'motion/react';

type BrowserMockupProps = {
  widgetVisible: boolean;
  motionComplete: boolean;
  motionActive: boolean;
  reducedMotion: boolean;
  testIds?: boolean;
  variant?: 'default' | 'hero';
};

export const WIDGET_LAUNCHER_REPEAT_DELAY_SECONDS = 10.7;

const heroWidgetVariants: Variants = {
  hidden: {
    opacity: 0,
    scale: 0.64,
    y: 44,
    rotate: 4,
  },
  visible: {
    opacity: [0, 1, 1],
    scale: [0.64, 1.08, 1],
    y: [44, -8, 0],
    rotate: [4, -0.7, 0],
  },
};

const miniatureFeatures = [
  ['Современный', 'и стильный дизайн'],
  ['Качество', 'и надёжность'],
  ['Индивидуальный', 'подход'],
  ['Прозрачные', 'условия'],
] as const;

export function BrowserMockup({
  widgetVisible,
  motionComplete,
  motionActive,
  reducedMotion,
  testIds = true,
  variant = 'default',
}: BrowserMockupProps) {
  const heroVariant = variant === 'hero';

  return (
    <div
      className={`browser-stack${heroVariant ? ' browser-stack--hero' : ''}`}
      data-testid={testIds ? 'browser-mockup' : undefined}
      data-variant={variant}
      data-motion-complete={motionComplete ? 'true' : 'false'}
      aria-hidden="true"
    >
      <div className="browser-stack__backing" aria-hidden="true" />
      <div className="browser-mockup">
        <div className="browser-mockup__chrome" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <div className="browser-mockup__nav">
          <strong>Modern House</strong>
          <span>Проекты</span>
          <span>Услуги</span>
          <span>Цены</span>
          <span>О компании</span>
          <span>Контакты</span>
        </div>
        <div className="browser-mockup__hero">
          <div className="browser-mockup__copy">
            <p>Строим дома,<br />в которых хочется<br />жить</p>
            <span>Оставить заявку</span>
          </div>
          <div className="browser-mockup__landscape" aria-hidden="true">
            <span className="landscape-blob landscape-blob--one" />
            <span className="landscape-blob landscape-blob--two" />
            <img src="/assets/house-cutout.png" alt="" />
          </div>
        </div>
        <div className="browser-mockup__features">
          {miniatureFeatures.map(([title, copy], index) => {
            const Icon = [FileText, ShieldCheck, Heart, FileText][index];
            return (
              <div key={title}>
                <Icon size={17} weight="regular" aria-hidden="true" />
                <span>{title}<br />{copy}</span>
              </div>
            );
          })}
        </div>
        <div className="browser-mockup__footer">
          <p>Воплощаем мечты<br />в надёжные дома</p>
          <span aria-hidden="true" />
          <span aria-hidden="true" />
        </div>
      </div>

      <motion.div
        className="widget-preview"
        data-testid={testIds ? 'widget-preview' : undefined}
        data-visible={widgetVisible ? 'true' : 'false'}
        data-variant={variant}
        initial={false}
        animate={
          heroVariant
            ? widgetVisible ? 'visible' : 'hidden'
            : {
              opacity: widgetVisible ? 1 : 0,
              scale: widgetVisible ? 1 : 0.82,
              y: widgetVisible ? 0 : 18,
            }
        }
        variants={heroVariant ? heroWidgetVariants : undefined}
        transition={
          reducedMotion
            ? { duration: 0, delay: 0 }
            : heroVariant
              ? widgetVisible
                ? { duration: 0.92, times: [0, 0.72, 1], ease: [0.16, 1, 0.3, 1] }
                : { duration: 0.32, ease: 'easeOut' }
              : { type: 'spring', stiffness: 120, damping: 18 }
        }
        aria-hidden={!widgetVisible}
      >
        {heroVariant ? (
          <>
            <span className="widget-preview__halo" aria-hidden="true" />
            <span className="widget-preview__burst" aria-hidden="true" />
            <span className="widget-preview__shimmer" aria-hidden="true" />
          </>
        ) : null}
        <div className="widget-preview__message">
          <Sparkle size={20} weight="fill" aria-hidden="true" />
          <span>Я изучил ваш сайт.<br />Чем помочь?</span>
        </div>
        <motion.span
          className="widget-preview__launcher"
          animate={
            motionComplete && motionActive && !reducedMotion
              ? { scale: [1, 1.08, 1] }
              : { scale: 1 }
          }
          transition={
            motionComplete && motionActive && !reducedMotion
              ? {
                duration: 2.3,
                repeat: Infinity,
                repeatDelay: WIDGET_LAUNCHER_REPEAT_DELAY_SECONDS,
                ease: 'easeInOut',
              }
              : { duration: 0 }
          }
        >
          <ArrowRight size={18} weight="bold" aria-hidden="true" />
        </motion.span>
      </motion.div>
    </div>
  );
}
