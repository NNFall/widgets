import { ArrowRight, FileText, Heart, ShieldCheck, Sparkle } from '@phosphor-icons/react';
import { motion, type Variants } from 'motion/react';

import { WidgetPreviewCard } from './WidgetPreviewCard';

type SiteVariant = 'architecture' | 'bakery' | 'ceramics';

type BrowserMockupProps = {
  widgetVisible: boolean;
  motionComplete: boolean;
  motionActive: boolean;
  reducedMotion: boolean;
  testIds?: boolean;
  variant?: 'default' | 'hero';
  siteVariant?: SiteVariant;
};

type SiteConfig = {
  brand: string;
  domain: string;
  nav: readonly string[];
  headline: readonly string[];
  action: string;
  image: string;
  features: readonly (readonly [string, string])[];
  footer: readonly string[];
  question: string;
};

export const WIDGET_LAUNCHER_REPEAT_DELAY_SECONDS = 10.7;

const heroWidgetVariants: Variants = {
  hidden: { opacity: 0, scale: 0.64, y: 44, rotate: 4 },
  visible: {
    opacity: [0, 1, 1],
    scale: [0.64, 1.08, 1],
    y: [44, -8, 0],
    rotate: [4, -0.7, 0],
  },
};

const sites: Record<SiteVariant, SiteConfig> = {
  architecture: {
    brand: 'Modern House',
    domain: 'modern-house.ru',
    nav: ['Проекты', 'Услуги', 'Цены', 'О компании', 'Контакты'],
    headline: ['Строим дома,', 'в которых хочется', 'жить'],
    action: 'Оставить заявку',
    image: '/assets/house-cutout.png',
    features: [
      ['Современный', 'дизайн'],
      ['Надёжные', 'материалы'],
      ['Индивидуальный', 'проект'],
      ['Понятные', 'условия'],
    ],
    footer: ['Воплощаем мечты', 'в надёжные дома'],
    question: 'Какой дом подойдёт для семьи?',
  },
  bakery: {
    brand: 'Тёплый хлеб',
    domain: 'teply-hleb.ru',
    nav: ['Меню', 'Торты', 'Доставка', 'О пекарне', 'Контакты'],
    headline: ['Свежая выпечка,', 'к которой хочется', 'возвращаться'],
    action: 'Смотреть меню',
    image: '/assets/bakery-cutout.png',
    features: [
      ['Каждый день', 'свежая выпечка'],
      ['Торты', 'на заказ'],
      ['Доставка', 'по городу'],
      ['Составы', 'без загадок'],
    ],
    footer: ['Печём утром,', 'доставляем сегодня'],
    question: 'Какие торты можно заказать?',
  },
  ceramics: {
    brand: 'Тихая форма',
    domain: 'tihaya-forma.ru',
    nav: ['Коллекции', 'Мастерская', 'Доставка', 'О нас', 'Контакты'],
    headline: ['Посуда, которую', 'хочется держать', 'в руках'],
    action: 'Смотреть коллекцию',
    image: '/assets/ceramics-cutout.png',
    features: [
      ['Ручная', 'работа'],
      ['Небольшие', 'тиражи'],
      ['Надёжная', 'упаковка'],
      ['Доставка', 'по России'],
    ],
    footer: ['Вещи с характером,', 'созданные вручную'],
    question: 'Что есть в наличии сейчас?',
  },
};

export function BrowserMockup({
  widgetVisible,
  motionComplete,
  motionActive,
  reducedMotion,
  testIds = true,
  variant = 'default',
  siteVariant = 'architecture',
}: BrowserMockupProps) {
  const heroVariant = variant === 'hero';
  const site = sites[siteVariant];

  return (
    <div
      className={`browser-stack browser-stack--${siteVariant}${heroVariant ? ' browser-stack--hero' : ''}`}
      data-testid={testIds ? 'browser-mockup' : undefined}
      data-variant={variant}
      data-site-variant={siteVariant}
      data-motion-complete={motionComplete ? 'true' : 'false'}
      aria-hidden="true"
    >
      <div className="browser-stack__backing" aria-hidden="true" />
      <div className="browser-mockup">
        <div className="browser-mockup__chrome" aria-hidden="true">
          <span /><span /><span />
          <div className="browser-mockup__address">
            <span>Ваш сайт</span>
            <strong>{site.domain}</strong>
          </div>
        </div>
        <div className="browser-mockup__nav">
          <strong>{site.brand}</strong>
          {site.nav.map((item) => <span key={item}>{item}</span>)}
        </div>
        <div className="browser-mockup__hero">
          <div className="browser-mockup__copy">
            <p>{site.headline.map((line) => <span className="browser-mockup__headline-line" key={line}>{line}</span>)}</p>
            <span className="browser-mockup__action">{site.action}</span>
          </div>
          <div className="browser-mockup__landscape" aria-hidden="true">
            <span className="landscape-blob landscape-blob--one" />
            <span className="landscape-blob landscape-blob--two" />
            <img
              src={site.image}
              alt=""
              loading={heroVariant ? 'eager' : 'lazy'}
              fetchPriority={heroVariant ? 'high' : 'auto'}
              decoding="async"
            />
          </div>
        </div>
        <div className="browser-mockup__features">
          {site.features.map(([title, copy], index) => {
            const Icon = [FileText, ShieldCheck, Heart, Sparkle][index];
            return (
              <div key={`${title}-${copy}`}>
                <Icon size={17} weight="regular" aria-hidden="true" />
                <span>{title}<br />{copy}</span>
              </div>
            );
          })}
        </div>
        <div className="browser-mockup__footer">
          <p>{site.footer[0]}<br />{site.footer[1]}</p><span aria-hidden="true" /><span aria-hidden="true" />
        </div>
      </div>

      <motion.div
        className="widget-preview"
        data-testid={testIds ? 'widget-preview' : undefined}
        data-visible={widgetVisible ? 'true' : 'false'}
        data-variant={variant}
        data-widget-shape="vertical"
        initial={false}
        animate={heroVariant
          ? widgetVisible ? 'visible' : 'hidden'
          : { opacity: widgetVisible ? 1 : 0, scale: widgetVisible ? 1 : 0.82, y: widgetVisible ? 0 : 18 }}
        variants={heroVariant ? heroWidgetVariants : undefined}
        transition={reducedMotion
          ? { duration: 0, delay: 0 }
          : heroVariant
            ? widgetVisible
              ? { duration: 0.92, times: [0, 0.72, 1], ease: [0.16, 1, 0.3, 1] }
              : { duration: 0.32, ease: 'easeOut' }
            : { type: 'spring', stiffness: 120, damping: 18 }}
        aria-hidden={!widgetVisible}
      >
        {heroVariant ? (
          <>
            <span className="widget-preview__halo" aria-hidden="true" />
            <span className="widget-preview__burst" aria-hidden="true" />
            <span className="widget-preview__shimmer" aria-hidden="true" />
          </>
        ) : null}
        <WidgetPreviewCard
          question={site.question}
          launcher={(
            <motion.span
              className="widget-preview__launcher"
              animate={motionComplete && motionActive && !reducedMotion ? { scale: [1, 1.08, 1] } : { scale: 1 }}
              transition={motionComplete && motionActive && !reducedMotion
                ? { duration: 2.3, repeat: Infinity, repeatDelay: WIDGET_LAUNCHER_REPEAT_DELAY_SECONDS, ease: 'easeInOut' }
                : { duration: 0 }}
            >
              <ArrowRight size={16} weight="bold" aria-hidden="true" />
            </motion.span>
          )}
        />
      </motion.div>
    </div>
  );
}
