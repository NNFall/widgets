import { ArrowLeft, ArrowRight } from '@phosphor-icons/react';
import { useEffect, useState } from 'react';

import { studioHref } from '../shared/campaign';
import { LANDING_MOBILE_MEDIA_QUERY } from '../shared/mobileLayout';
import { useMotionActivity } from '../shared/MotionActivity';
import {
  TourIntakeVisual,
  TourPublishVisual,
  TourStudioVisual,
} from './ProductTourVisuals';

const AUTOPLAY_MS = 8_000;

const tourScenes = [
  {
    label: 'Добавьте сайт',
    kicker: 'Этап 1 из 3',
    title: 'Дайте Kaigo ссылку на ваш сайт',
    copy: 'Вставьте адрес действующего сайта. Если есть пожелание — напишите его рядом обычными словами. Анкета, макет и техническое задание не нужны.',
    note: 'Можно начать бесплатно. Карта не нужна.',
    instructions: [
      'Укажите публичную ссылку на сайт бизнеса.',
      'Коротко напишите, что важно учесть в общении или внешнем виде.',
      'Запустите бесплатную экспресс-версию — дальше Kaigo сам изучит страницы.',
    ],
    Visual: TourIntakeVisual,
  },
  {
    label: 'Проверьте результат',
    kicker: 'Этап 2 из 3',
    title: 'Примерно через 10 минут проверьте результат',
    copy: 'Готовая первая версия откроется в Studio. Там сразу видно внешний вид виджета и можно поговорить с ним как обычный посетитель сайта.',
    note: 'Первая версия бесплатна для одного подтверждённого аккаунта.',
    instructions: [
      'Откройте виджет на компьютере или в мобильном режиме.',
      'Задайте вопросы об услугах, стоимости и условиях.',
      'Если результат подходит — переходите к публикации. Доработки открываются после выбора тарифа.',
    ],
    Visual: TourStudioVisual,
  },
  {
    label: 'Подключите к сайту',
    kicker: 'Этап 3 из 3',
    title: 'Добавьте AI-консультанта на сайт',
    copy: 'После вашей проверки Kaigo выдаст одну строку кода. Вставьте её один раз в настройки сайта — и виджет появится на нужных страницах.',
    note: 'Сам сайт переделывать не нужно.',
    instructions: [
      'На Tilda: откройте «Настройки сайта → Ещё → HTML-код для вставки» и добавьте строку перед закрывающим тегом body.',
      'На другом сайте: вставьте строку перед закрывающим тегом body или передайте её разработчику.',
      'Опубликуйте изменения. Дальше содержанием и версиями вы управляете в Kaigo Studio.',
    ],
    Visual: TourPublishVisual,
  },
] as const;

type ProductTourProps = {
  standalone?: boolean;
};

export function ProductTour({ standalone = false }: ProductTourProps) {
  const [activeStep, setActiveStep] = useState(0);
  const [isMobile, setIsMobile] = useState(false);
  const [focusWithin, setFocusWithin] = useState(false);
  const [pointerInside, setPointerInside] = useState(false);
  const campaignStudioHref = studioHref();
  const { active: motionActive, reducedMotion, ref } = useMotionActivity<HTMLElement>();

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return undefined;
    }

    const mediaQuery = window.matchMedia(LANDING_MOBILE_MEDIA_QUERY);
    if (!mediaQuery) return undefined;

    const syncMobileState = () => setIsMobile(mediaQuery.matches);

    syncMobileState();
    if (typeof mediaQuery.addEventListener !== 'function') return undefined;

    mediaQuery.addEventListener('change', syncMobileState);

    return () => {
      if (typeof mediaQuery.removeEventListener === 'function') {
        mediaQuery.removeEventListener('change', syncMobileState);
      }
    };
  }, []);

  const autoPlay = motionActive && !reducedMotion && !focusWithin && !pointerInside && !isMobile;

  useEffect(() => {
    if (!autoPlay) return undefined;

    const timer = window.setTimeout(() => {
      setActiveStep((step) => (step + 1) % tourScenes.length);
    }, AUTOPLAY_MS);

    return () => window.clearTimeout(timer);
  }, [activeStep, autoPlay]);

  const showStep = (index: number) => setActiveStep(
    (index + tourScenes.length) % tourScenes.length,
  );

  return (
    <section
      className={`product-tour-section${standalone ? ' product-tour-section--standalone' : ''}`}
      aria-label="Как Kaigo создаёт AI-сотрудника"
      data-active-step={activeStep + 1}
      data-autoplay={autoPlay ? 'true' : 'false'}
      data-motion-active={motionActive ? 'true' : 'false'}
      id="product-tour"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setFocusWithin(false);
      }}
      onFocus={() => setFocusWithin(true)}
      onPointerEnter={() => setPointerInside(true)}
      onPointerLeave={() => setPointerInside(false)}
      ref={ref}
      {...(!standalone ? { 'data-landing-section': true } : {})}
    >
      <div className="product-tour__topline">
        <div>
          <span>Реальный кейс · FORMA</span>
          <strong>От одной ссылки до AI-консультанта на сайте</strong>
        </div>
      </div>

      <div className="product-tour__viewport" aria-live={autoPlay ? 'off' : 'polite'}>
        {tourScenes.map((scene, index) => (
          <article
            aria-hidden={index !== activeStep}
            aria-labelledby={`product-tour-title-${index + 1}`}
            className="product-tour__scene"
            data-active={index === activeStep ? 'true' : 'false'}
            data-tour-step={index + 1}
            hidden={index !== activeStep}
            key={scene.title}
          >
            <div className="product-tour__copy">
              <span className="product-tour__kicker">{scene.kicker}</span>
              <h2 id={`product-tour-title-${index + 1}`}>{scene.title}</h2>
              <p>{scene.copy}</p>
              <p className="product-tour__note">{scene.note}</p>
              <ol className="product-tour__instructions">
                {scene.instructions.map((instruction, instructionIndex) => (
                  <li key={instruction}>
                    <span>{instructionIndex + 1}</span>
                    <p>{instruction}</p>
                  </li>
                ))}
              </ol>
            </div>
            <div className="product-tour__visual"><scene.Visual /></div>
          </article>
        ))}
      </div>

      <div className="product-tour__controls">
        <div className="product-tour__arrows">
          <button aria-label="Предыдущий этап" onClick={() => showStep(activeStep - 1)} type="button">
            <ArrowLeft size={20} aria-hidden /><span>Назад</span>
          </button>
          <button aria-label="Следующий этап" onClick={() => showStep(activeStep + 1)} type="button">
            <span>Дальше</span><ArrowRight size={20} aria-hidden />
          </button>
        </div>
        <nav className="product-tour__steps" aria-label="Этапы создания AI-сотрудника" data-mobile-snap="true">
          {tourScenes.map((scene, index) => (
            <button
              aria-label={`Показать этап ${index + 1}: ${scene.title}`}
              aria-pressed={activeStep === index}
              data-active={activeStep === index ? 'true' : 'false'}
              key={scene.title}
              onClick={() => showStep(index)}
              type="button"
            >
              <span>Этап {index + 1}</span><strong>{scene.label}</strong>
            </button>
          ))}
        </nav>
        <a className="product-tour__cta" href={campaignStudioHref}>Создать бесплатную версию<ArrowRight size={20} weight="bold" aria-hidden /></a>
      </div>
    </section>
  );
}
