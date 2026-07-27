import { ArrowRight, CheckCircle, Eye, UploadSimple } from '@phosphor-icons/react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { useState } from 'react';

import { BrowserMockup } from '../shared/BrowserMockup';
import { Reveal } from '../shared/Reveal';

const suggestions = [
  'Сделать ответы короче и дружелюбнее',
  'Добавить вопрос о площади участка',
  'Сделать акцент на готовых проектах',
] as const;

export function StudioSection() {
  const [request, setRequest] = useState<string>(suggestions[0]);
  const [revision, setRevision] = useState(4);
  const reducedMotion = Boolean(useReducedMotion());

  const applyRevision = () => {
    if (!request.trim()) return;
    setRevision((value) => value + 1);
  };

  return (
    <section className="landing-section studio-section" id="studio-showcase" data-landing-section>
      <div className="landing-shell studio-layout">
        <Reveal className="studio-copy">
          <p className="section-kicker">Студия Kaigo</p>
          <h2 aria-label="Первый вариант — только начало"><span>Первый вариант —</span><span>только начало</span></h2>
          <p>Откройте результат в студии: попросите изменить приветствие, характер общения, внешний вид или сценарий. Каждая версия остаётся доступной для сравнения.</p>
          <a className="primary-button primary-button--wide" href="/studio">Перейти в студию</a>
          <span className="studio-copy__note"><CheckCircle size={22} />Публикация только после вашей проверки</span>
        </Reveal>

        <Reveal className="studio-demo" delay={0.08}>
          <div className="studio-demo__sidebar">
            <h3>Что изменить?</h3>
            <label className="sr-only" htmlFor="studio-refinement">Пожелание к виджету</label>
            <textarea id="studio-refinement" value={request} onChange={(event) => setRequest(event.target.value)} />
            <button type="button" aria-label="Применить изменение" onClick={applyRevision}><ArrowRight size={18} weight="bold" /></button>
            <strong>История версий</strong>
            <ol>
              {[revision, revision - 1, revision - 2, revision - 3].map((version, index) => (
                <li className={index === 0 ? 'is-current' : ''} key={version}><i />Версия {version}<span /></li>
              ))}
            </ol>
          </div>
          <div className="studio-demo__workspace">
            <div className="studio-demo__toolbar">
              <span className="studio-demo__version">Версия {revision}</span>
              <span className="studio-demo__action studio-demo__preview-label"><Eye size={18} />Предпросмотр</span>
              <span className="studio-demo__action studio-demo__publish-label"><UploadSimple size={18} />Опубликовать</span>
            </div>
            <AnimatePresence mode="wait" initial={false}>
              <motion.div
                className="studio-demo__preview"
                key={revision}
                initial={reducedMotion ? false : { opacity: 0, x: 14 }}
                animate={{ opacity: 1, x: 0 }}
                exit={reducedMotion ? undefined : { opacity: 0, x: -14 }}
                transition={{ duration: reducedMotion ? 0 : 0.32 }}
              >
                <BrowserMockup widgetVisible motionComplete reducedMotion={reducedMotion} testIds={false} />
                <span className="studio-demo__revision-note">{request}</span>
              </motion.div>
            </AnimatePresence>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
