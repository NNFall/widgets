import { ArrowLeft, ArrowRight, ArrowsOutSimple, CheckCircle } from '@phosphor-icons/react';
import { useState } from 'react';

import { studioHref } from '../shared/campaign';
import { useMotionActivity } from '../shared/MotionActivity';
import {
  TourAnalysisVisual,
  TourIntakeVisual,
  TourPublishVisual,
  TourStudioVisual,
} from './ProductTourVisuals';

const tourScenes = [
  {
    label: 'Ссылка и идея',
    kicker: 'Шаг 1 · около минуты',
    title: 'От вас нужны ссылка и одна фраза',
    copy: 'Покажите действующий сайт и коротко напишите, каким должен быть помощник. Техническое задание и длинная анкета не нужны.',
    facts: ['Ссылка на публичный сайт', 'Одно необязательное пожелание'],
    Visual: TourIntakeVisual,
  },
  {
    label: 'Сборка',
    kicker: 'Шаг 2 · Kaigo работает сам',
    title: 'Kaigo изучает сайт и собирает AI-сотрудника',
    copy: 'Сервис открывает страницы, находит услуги, условия и стиль общения. На основе этого контекста он готовит внешний вид и сценарий разговора.',
    facts: ['Контент и услуги', 'Дизайн и тон общения'],
    Visual: TourAnalysisVisual,
  },
  {
    label: 'Проверка',
    kicker: 'Шаг 3 · до оплаты',
    title: 'Через 10–20 минут проверяете результат в Studio',
    copy: 'Посмотрите виджет на компьютере и телефоне, поговорите с ним как клиент и напишите пожелание к следующей версии.',
    facts: ['Живой тест диалога', 'Предпросмотр двух форматов'],
    Visual: TourStudioVisual,
  },
  {
    label: 'На сайте',
    kicker: 'Шаг 4 · запуск под вашим контролем',
    title: 'Одна строка кода, и AI-сотрудник уже на сайте',
    copy: 'После проверки выберите тариф и публикацию. Kaigo выдаст строку установки, а готовый консультант появится на нужных страницах.',
    facts: ['Публикация только после подтверждения', 'Готовая строка для установки'],
    Visual: TourPublishVisual,
  },
] as const;

type ProductTourProps = {
  standalone?: boolean;
};

export function ProductTour({ standalone = false }: ProductTourProps) {
  const [activeStep, setActiveStep] = useState(0);
  const campaignStudioHref = studioHref();
  const { active: motionActive, ref } = useMotionActivity<HTMLElement>();

  return (
    <section
      className={`product-tour-section${standalone ? ' product-tour-section--standalone' : ''}`}
      aria-label="Как Kaigo создаёт AI-сотрудника"
      data-active-step={activeStep + 1}
      data-motion-active={motionActive ? 'true' : 'false'}
      id="product-tour"
      ref={ref}
      {...(!standalone ? { 'data-landing-section': true } : {})}
    >
      <div className="product-tour__topline">
        <div>
          <span>Наглядно, за четыре шага</span>
          <strong>Что именно вы получите от Kaigo</strong>
        </div>
        {!standalone && (
          <a className="product-tour__standalone-link" href="/tour">
            Открыть демонстрацию отдельно<ArrowsOutSimple size={19} aria-hidden />
          </a>
        )}
      </div>

      <div className="product-tour__viewport" aria-live="polite">
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
              <ul>
                {scene.facts.map((fact) => <li key={fact}><CheckCircle size={20} weight="fill" aria-hidden />{fact}</li>)}
              </ul>
            </div>
            <div className="product-tour__visual"><scene.Visual /></div>
          </article>
        ))}
      </div>

      <div className="product-tour__controls">
        <div className="product-tour__arrows">
          <button
            aria-label="Предыдущий этап"
            disabled={activeStep === 0}
            onClick={() => setActiveStep((step) => Math.max(0, step - 1))}
            type="button"
          >
            <ArrowLeft size={22} aria-hidden />
          </button>
          <button
            aria-label="Следующий этап"
            disabled={activeStep === tourScenes.length - 1}
            onClick={() => setActiveStep((step) => Math.min(tourScenes.length - 1, step + 1))}
            type="button"
          >
            <ArrowRight size={22} aria-hidden />
          </button>
        </div>
        <nav className="product-tour__steps" aria-label="Этапы создания AI-сотрудника">
        {tourScenes.map((scene, index) => (
          <button
            aria-label={`Показать этап ${index + 1}: ${scene.title}`}
            aria-pressed={activeStep === index}
            data-active={activeStep === index ? 'true' : 'false'}
            key={scene.title}
            onClick={() => setActiveStep(index)}
            type="button"
          >
            <span>0{index + 1}</span><small>{scene.label}</small>
          </button>
        ))}
        </nav>
        <a className="product-tour__cta" href={campaignStudioHref}>Создать бесплатную версию<ArrowRight size={20} weight="bold" aria-hidden /></a>
      </div>
    </section>
  );
}
