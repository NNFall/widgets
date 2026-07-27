import { CheckCircle, ShieldCheck, Sparkle } from '@phosphor-icons/react';
import { useReducedMotion } from 'motion/react';
import { useState } from 'react';

import { BrowserMockup } from '../shared/BrowserMockup';
import { Reveal } from '../shared/Reveal';

export function CaseStudySection() {
  const [activeView, setActiveView] = useState<'before' | 'after'>('after');
  const reducedMotion = Boolean(useReducedMotion());

  return (
    <section className="landing-section case-section" id="case-study" data-landing-section>
      <div className="landing-shell">
        <Reveal className="case-heading">
          <p className="section-kicker section-kicker--pill">Кейс: AI-консультант для строительной компании</p>
          <h2>Один и тот же сайт<br />до и после Kaigo</h2>
        </Reveal>

        <div className="case-toggle" role="group" aria-label="Показать состояние сайта">
          <button type="button" aria-pressed={activeView === 'before'} onClick={() => setActiveView('before')}>До</button>
          <button type="button" aria-pressed={activeView === 'after'} onClick={() => setActiveView('after')}>После</button>
        </div>

        <div className="case-comparison">
          <Reveal className={`case-panel case-panel--before${activeView === 'before' ? ' is-mobile-active' : ''}`}>
            <div className="case-panel__label"><span>До</span><p>Посетитель сам ищет проекты, условия и способ оставить заявку.</p></div>
            <div className="case-browser"><BrowserMockup widgetVisible={false} motionComplete={false} reducedMotion testIds={false} /></div>
          </Reveal>
          <Reveal className={`case-panel case-panel--after${activeView === 'after' ? ' is-mobile-active' : ''}`} delay={0.08}>
            <div className="case-panel__label"><span>После</span><p>AI-виджет отвечает по услугам, помогает выбрать проект и подводит к обращению.</p></div>
            <div className="case-browser"><BrowserMockup widgetVisible motionComplete reducedMotion={reducedMotion} testIds={false} /></div>
          </Reveal>
        </div>

        <Reveal className="case-footer">
          <span><CheckCircle size={25} />Знает услуги</span>
          <span><ShieldCheck size={25} />Сохраняет стиль сайта</span>
          <span><Sparkle size={25} />Помогает сделать следующий шаг</span>
          <a className="primary-button" href="#how-it-works">Посмотреть, как создавался виджет</a>
        </Reveal>
      </div>
    </section>
  );
}
