import { Code, Eye, ShieldCheck } from '@phosphor-icons/react';
import { motion, useReducedMotion } from 'motion/react';
import type { Variants } from 'motion/react';

import { KaigoLogo } from '../shared/KaigoLogo';
import { UrlComposer } from '../shared/UrlComposer';
import { WidgetPreviewCard } from '../shared/WidgetPreviewCard';
import { studioHref } from '../shared/campaign';

const guarantees = [
  { label: 'Без кода', Icon: Code },
  { label: 'Сначала предпросмотр', Icon: Eye },
  { label: 'Публикация после проверки', Icon: ShieldCheck },
] as const;

const finalCtaSequence = {
  hidden: {},
  visible: { transition: { delayChildren: 0.08, staggerChildren: 0.15 } },
} satisfies Variants;

const finalCopyVariants = {
  hidden: { opacity: 0, y: 24 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.64, ease: [0.16, 1, 0.3, 1] } },
} satisfies Variants;

const beforeCardVariants = {
  hidden: { opacity: 0.36, x: 'var(--final-card-travel)', y: 16, scale: 0.96 },
  visible: { opacity: 0.72, x: 0, y: 0, scale: 1, transition: { type: 'spring', stiffness: 92, damping: 16 } },
} satisfies Variants;

const afterCardVariants = {
  hidden: { opacity: 0.38, x: 'calc(0px - var(--final-card-travel))', y: 18, scale: 0.95 },
  visible: { opacity: 1, x: 0, y: 0, scale: 1, transition: { type: 'spring', stiffness: 104, damping: 15, delay: 0.08 } },
} satisfies Variants;

const composerVariants = {
  hidden: { opacity: 0, y: 32, scale: 0.975 },
  visible: { opacity: 1, y: 0, scale: 1, transition: { type: 'spring', stiffness: 112, damping: 17 } },
} satisfies Variants;

const guaranteeRowVariants = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.09 } },
} satisfies Variants;

const guaranteeVariants = {
  hidden: { opacity: 0, y: 14 },
  visible: { opacity: 1, y: 0, transition: { type: 'spring', stiffness: 120, damping: 18 } },
} satisfies Variants;

const finalWidgetVariants = {
  hidden: { opacity: 0, y: 24, scale: 0.62 },
  visible: { opacity: 1, y: 0, scale: 1, transition: { type: 'spring', stiffness: 190, damping: 13, delay: 0.48 } },
} satisfies Variants;

const finalWidgetHaloVariants = {
  hidden: { opacity: 0, scale: 0.72 },
  visible: { opacity: [0, 0.92, 0], scale: [0.72, 1.08, 1.22], transition: { duration: 1.05, delay: 0.46 } },
} satisfies Variants;

export function FinalCtaSection() {
  const reducedMotion = Boolean(useReducedMotion());
  const campaignStudioHref = studioHref();
  const viewportMotionAvailable = import.meta.env.MODE !== 'test'
    && typeof IntersectionObserver !== 'undefined';

  return (
    <section className="landing-section final-cta-section" id="final-cta" data-landing-section>
      <motion.div
        className="landing-shell final-cta-content final-cta-motion"
        data-layout="split"
        initial={reducedMotion || !viewportMotionAvailable ? false : 'hidden'}
        animate={reducedMotion ? 'visible' : undefined}
        whileInView="visible"
        viewport={{ once: true, amount: 0.18 }}
        variants={finalCtaSequence}
      >
        <motion.div className="final-cta-copy" variants={finalCopyVariants}>
          <p className="section-kicker">Можно начать прямо сейчас</p>
          <h2>Получите бесплатную экспресс-версию и проверьте её сами</h2>
          <p>Обычно первая версия готова примерно за 10–20 минут. Откройте её в Studio, задайте несколько вопросов и только потом решайте, нужна ли публикация.</p>
        </motion.div>
        <motion.div className="final-cta-visual" variants={finalCopyVariants}>
          <motion.div
            className="final-site-card__motion final-site-card__motion--before"
            variants={beforeCardVariants}
          >
            <span className="final-site-card__label">Сайт без Kaigo</span>
            <div className="final-site-card final-site-card--before"><MiniSite after={false} /></div>
          </motion.div>
          <motion.div
            className="final-site-card__motion final-site-card__motion--after"
            variants={afterCardVariants}
          >
            <span className="final-site-card__label">Сайт с AI-консультантом</span>
            <div className="final-site-card final-site-card--after"><MiniSite after /></div>
          </motion.div>
        </motion.div>
        <motion.div className="final-composer" variants={composerVariants}>
          <UrlComposer
            ariaLabel="Ссылка на сайт — финальная форма"
            submitAriaLabel="Получить бесплатную версию"
          />
          <small className="final-composer__note">Нужна только публичная ссылка на сайт. Карту не попросим.</small>
        </motion.div>
        <motion.div className="guarantee-row" variants={guaranteeRowVariants}>
          {guarantees.map(({ label, Icon }) => (
            <motion.span key={label} variants={guaranteeVariants}><Icon size={30} weight="regular" />{label}</motion.span>
          ))}
        </motion.div>
      </motion.div>
      <footer className="site-footer">
        <div className="landing-shell site-footer__inner">
          <div><KaigoLogo tone="coral" /><p>Персональные AI-виджеты<br />для бизнеса.</p></div>
          <nav aria-label="Навигация в подвале">
            <a href="#product">Продукт</a><a href="#product-tour">Как это работает</a><a href="#case-study">Кейсы</a><a href="#faq">Помощь</a><a href={campaignStudioHref}>Студия</a>
          </nav>
          <div className="site-footer__legal"><span>Политика конфиденциальности</span><span>Условия использования</span></div>
        </div>
      </footer>
      <span id="landing-scroll-end" className="landing-scroll-end" aria-hidden="true" />
    </section>
  );
}

function MiniSite({ after }: { after: boolean }) {
  return (
    <div className={`mini-site mini-site--${after ? 'after' : 'before'}`} aria-hidden="true">
      <div className="mini-site__chrome"><i /><i /><i /></div>
      <div className="mini-site__nav"><strong>Тёплый хлеб</strong><span>Меню</span><span>Торты</span><span>Доставка</span></div>
      <div className="mini-site__main"><p>Свежая выпечка<br />каждое утро</p><img src="/assets/bakery-cutout.png" alt="" loading="lazy" decoding="async" /></div>
      {after ? (
        <motion.div className="mini-site__widget-shell" variants={finalWidgetVariants}>
          <motion.span className="mini-site__widget-halo" variants={finalWidgetHaloVariants} />
          <WidgetPreviewCard compact question="Какие торты можно заказать?" />
        </motion.div>
      ) : null}
    </div>
  );
}
