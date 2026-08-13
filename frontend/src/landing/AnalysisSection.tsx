import { ChatCircleDots, FileText, Palette, TreeStructure } from '@phosphor-icons/react';
import { useEffect, useState } from 'react';

import { BrowserMockup } from '../shared/BrowserMockup';
import { useMotionActivity } from '../shared/MotionActivity';
import { Reveal } from '../shared/Reveal';

const observations = [
  { target: 'content', label: 'Содержание', copy: 'Услуги, цены, условия и ответы', Icon: FileText, className: 'analysis-note--content' },
  { target: 'visual', label: 'Визуальный язык', copy: 'Цвета, типографика и характер бренда', Icon: Palette, className: 'analysis-note--visual' },
  { target: 'structure', label: 'Структура', copy: 'Разделы, навигация и путь клиента', Icon: TreeStructure, className: 'analysis-note--structure' },
  { target: 'questions', label: 'Вопросы посетителей', copy: 'Что людям важно узнать перед обращением', Icon: ChatCircleDots, className: 'analysis-note--questions' },
] as const;

const ANALYSIS_OBSERVATION_STEP_MS = 1_800;

function useAnalysisObservationCycle(active: boolean) {
  const [index, setIndex] = useState(0);

  useEffect(() => {
    if (!active) {
      setIndex(0);
      return;
    }

    const timer = window.setTimeout(() => {
      setIndex((current) => (current + 1) % observations.length);
    }, ANALYSIS_OBSERVATION_STEP_MS);

    return () => window.clearTimeout(timer);
  }, [active, index]);

  return active ? observations[index].target : observations[0].target;
}

export function AnalysisSection() {
  const { active, reducedMotion, ref } = useMotionActivity<HTMLElement>();
  const observation = useAnalysisObservationCycle(active);

  return (
    <section
      className="landing-section analysis-section"
      id="analysis"
      data-landing-section
      data-motion-active={active ? 'true' : 'false'}
      ref={ref}
    >
      <div className="landing-shell analysis-layout">
        <Reveal className="analysis-copy" preset="heading">
          <p className="section-kicker">Визуальный анализ</p>
          <h2 aria-label="Что видит Kaigo">Kaigo видит не только текст сайта</h2>
          <p>Он изучает страницы, услуги, цены, оформление и путь клиента. На этой основе собирается консультант, который знает ваш бизнес и выглядит частью сайта.</p>
          <div className="analysis-principle">
            <MagnifierMark />
            <span>Сначала Kaigo понимает сайт. Затем собирает виджет.</span>
          </div>
        </Reveal>

        <div className="analysis-scene">
          <Reveal className="analysis-browser__entrance" delay={0.08} preset="scale">
            <div className="analysis-browser">
              <BrowserMockup
                widgetVisible={false}
                motionComplete={active}
                motionActive={active}
                reducedMotion={reducedMotion}
                testIds={false}
                siteVariant="ceramics"
              />
            </div>
          </Reveal>
          <div
            className="analysis-focus-ring"
            data-analysis-focus={observation}
            data-testid="analysis-focus-ring"
            aria-hidden="true"
          />
          <span className="analysis-focus-node analysis-focus-node--content" data-analysis-target="content" aria-hidden="true" />
          <span className="analysis-focus-node analysis-focus-node--visual" data-analysis-target="visual" aria-hidden="true" />
          <span className="analysis-focus-node analysis-focus-node--structure" data-analysis-target="structure" aria-hidden="true" />
          <span className="analysis-focus-node analysis-focus-node--questions" data-analysis-target="questions" aria-hidden="true" />
          {observations.map(({ target }, index) => (
            <div
              className={`analysis-lens analysis-lens--${index + 1}`}
              data-analysis-active={observation === target ? 'true' : 'false'}
              data-analysis-kind="lens"
              data-analysis-observation={target}
              key={target}
              aria-hidden="true"
            />
          ))}
          {observations.map(({ target, label, copy, Icon, className }, index) => (
            <Reveal
              className={`analysis-note-shell ${className}`}
              delay={0.28 + index * 0.22}
              key={label}
              preset={index % 2 === 0 ? 'fromLeft' : 'fromRight'}
            >
              <article
                className="analysis-note"
                data-analysis-active={observation === target ? 'true' : 'false'}
                data-analysis-kind="note"
                data-analysis-observation={target}
              >
                <Icon size={37} weight="regular" aria-hidden="true" />
                <span><strong>{label}</strong><small>{copy}</small></span>
              </article>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}

function MagnifierMark() {
  return (
    <svg viewBox="0 0 52 52" aria-hidden="true">
      <circle cx="22" cy="22" r="13" /><path d="m32 32 11 11" /><path d="M22 13v6M22 25v1" />
    </svg>
  );
}
