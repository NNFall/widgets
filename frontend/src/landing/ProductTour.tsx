import { ArrowLeft, ArrowRight, Pause, Play } from '@phosphor-icons/react';
import { useEffect, useState } from 'react';

import { studioHref } from '../shared/campaign';
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
    title: 'Вставьте ссылку на ваш сайт',
    copy: 'Для старта не нужна анкета или техническое задание. Достаточно адреса сайта и пары предложений о том, каким вы хотите видеть помощника.',
    instructions: [
      'Вставьте ссылку на действующий сайт.',
      'Если есть пожелания, опишите их обычным текстом.',
      'Нажмите «Создать». Остальное Kaigo сделает сам.',
    ],
    Visual: TourIntakeVisual,
  },
  {
    label: 'Проверьте в Studio',
    kicker: 'Этап 2 из 3',
    title: 'Через 10–20 минут проверьте результат в Studio',
    copy: 'Kaigo сам изучит страницы, услуги и стиль общения. Первая версия появится в Studio, где её можно спокойно проверить до публикации и оплаты.',
    instructions: [
      'Откройте готовый виджет прямо в Studio.',
      'Задайте ему несколько вопросов как клиент.',
      'Сохраните пожелание. Доработки доступны после выбора тарифа.',
    ],
    Visual: TourStudioVisual,
  },
  {
    label: 'Подключите к сайту',
    kicker: 'Этап 3 из 3',
    title: 'Добавьте AI-сотрудника на сайт',
    copy: 'Когда ответы и внешний вид вас устраивают, опубликуйте версию. Сам сайт переделывать не придётся, Kaigo добавляется отдельно.',
    instructions: [
      'Подтвердите готовую версию и выберите публикацию.',
      'Скопируйте одну строку кода или передайте её разработчику.',
      'После установки виджет начнёт отвечать посетителям сайта.',
    ],
    Visual: TourPublishVisual,
  },
] as const;

type ProductTourProps = {
  standalone?: boolean;
};

export function ProductTour({ standalone = false }: ProductTourProps) {
  const [activeStep, setActiveStep] = useState(0);
  const [focusWithin, setFocusWithin] = useState(false);
  const [pointerInside, setPointerInside] = useState(false);
  const [autoPlayEnabled, setAutoPlayEnabled] = useState(true);
  const campaignStudioHref = studioHref();
  const { active: motionActive, reducedMotion, ref } = useMotionActivity<HTMLElement>();
  const autoPlay = motionActive && !reducedMotion && !focusWithin && !pointerInside && autoPlayEnabled;

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
          <span>Три понятных этапа</span>
          <strong>От ссылки до AI-сотрудника на вашем сайте</strong>
        </div>
        <button
          aria-label={reducedMotion
            ? 'Автолистание отключено настройками системы'
            : autoPlayEnabled ? 'Остановить автолистание' : 'Продолжить автолистание'}
          className="product-tour__autoplay"
          disabled={reducedMotion}
          onClick={() => setAutoPlayEnabled((enabled) => !enabled)}
          type="button"
        >
          {autoPlayEnabled ? <Pause size={17} weight="fill" aria-hidden /> : <Play size={17} weight="fill" aria-hidden />}
          <span>{reducedMotion
            ? 'Автолистание отключено'
            : autoPlayEnabled ? 'Автолистание включено' : 'Автолистание остановлено'}</span>
        </button>
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
        <nav className="product-tour__steps" aria-label="Этапы создания AI-сотрудника">
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
