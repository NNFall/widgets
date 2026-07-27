import { ChatCircleDots, FileText, Palette, TreeStructure } from '@phosphor-icons/react';

import { BrowserMockup } from '../shared/BrowserMockup';
import { useMotionActivity } from '../shared/MotionActivity';
import { Reveal } from '../shared/Reveal';

const observations = [
  { label: 'Содержание', copy: 'Услуги, цены, условия и ответы', Icon: FileText, className: 'analysis-note--content' },
  { label: 'Визуальный язык', copy: 'Цвета, типографика и характер бренда', Icon: Palette, className: 'analysis-note--visual' },
  { label: 'Структура', copy: 'Разделы, навигация и путь клиента', Icon: TreeStructure, className: 'analysis-note--structure' },
  { label: 'Вопросы посетителей', copy: 'Что людям важно узнать перед обращением', Icon: ChatCircleDots, className: 'analysis-note--questions' },
] as const;

export function AnalysisSection() {
  const { active, ref } = useMotionActivity<HTMLElement>();

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
          <h2 aria-label="Что видит Kaigo">Kaigo изучает сайт,<br />а не просто<br />читает текст</h2>
          <p>Он проходит по страницам, видит оформление и собирает контекст, который понадобится будущему AI-консультанту.</p>
          <div className="analysis-principle">
            <MagnifierMark />
            <span>Сначала визуальный анализ.<br />Затем — создание виджета.</span>
          </div>
        </Reveal>

        <div className="analysis-scene">
          <Reveal className="analysis-browser__entrance" delay={0.08} preset="scale">
            <div className="analysis-browser">
              <BrowserMockup
                widgetVisible={false}
                motionComplete={active}
                reducedMotion={!active}
                testIds={false}
              />
            </div>
          </Reveal>
          <div className="analysis-focus-ring" aria-hidden="true" />
          <span className="analysis-focus-node analysis-focus-node--content" data-analysis-target="content" aria-hidden="true" />
          <span className="analysis-focus-node analysis-focus-node--visual" data-analysis-target="visual" aria-hidden="true" />
          <span className="analysis-focus-node analysis-focus-node--structure" data-analysis-target="structure" aria-hidden="true" />
          <span className="analysis-focus-node analysis-focus-node--questions" data-analysis-target="questions" aria-hidden="true" />
          <div className="analysis-lens analysis-lens--one" aria-hidden="true" />
          <div className="analysis-lens analysis-lens--two" aria-hidden="true" />
          <div className="analysis-lens analysis-lens--three" aria-hidden="true" />
          {observations.map(({ label, copy, Icon, className }, index) => (
            <Reveal
              className={`analysis-note-shell ${className}`}
              delay={0.28 + index * 0.22}
              key={label}
              preset={index % 2 === 0 ? 'fromLeft' : 'fromRight'}
            >
              <article className="analysis-note">
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
