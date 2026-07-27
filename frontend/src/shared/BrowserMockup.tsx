import { ArrowRight, FileText, Heart, ShieldCheck, Sparkle } from '@phosphor-icons/react';
import { motion } from 'motion/react';

type BrowserMockupProps = {
  widgetVisible: boolean;
  motionComplete: boolean;
  reducedMotion: boolean;
  testIds?: boolean;
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
  reducedMotion,
  testIds = true,
}: BrowserMockupProps) {
  return (
    <div className="browser-stack" data-testid={testIds ? 'browser-mockup' : undefined} aria-hidden="true">
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
        initial={false}
        animate={{
          opacity: widgetVisible ? 1 : 0,
          scale: widgetVisible ? 1 : 0.82,
          y: widgetVisible ? 0 : 18,
        }}
        transition={{ type: 'spring', stiffness: 120, damping: 18 }}
        aria-hidden={!widgetVisible}
      >
        <div className="widget-preview__message">
          <Sparkle size={20} weight="fill" aria-hidden="true" />
          <span>Я изучил ваш сайт.<br />Чем помочь?</span>
        </div>
        <motion.span
          className="widget-preview__launcher"
          animate={
            motionComplete && !reducedMotion
              ? { scale: [1, 1.08, 1] }
              : { scale: 1 }
          }
          transition={
            motionComplete && !reducedMotion
              ? { duration: 2.3, repeat: Infinity, repeatDelay: 8.5, ease: 'easeInOut' }
              : { duration: 0 }
          }
        >
          <ArrowRight size={18} weight="bold" aria-hidden="true" />
        </motion.span>
      </motion.div>
    </div>
  );
}
