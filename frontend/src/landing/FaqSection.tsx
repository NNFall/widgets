import { ArrowRight, CaretDown, LinkSimple, Question } from '@phosphor-icons/react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { useState } from 'react';

import { Reveal } from '../shared/Reveal';
import { studioHref } from '../shared/campaign';

const questions = [
  {
    question: 'Подойдёт ли сайт на Tilda или WordPress?',
    answer: 'Да. Для первого анализа достаточно публичной ссылки на сайт — платформа и способ сборки страницы не важны.',
  },
  {
    question: 'Сколько времени занимает создание?',
    answer: 'Первую версию AI-виджета обычно можно получить за 10–20 минут. Сложные сайты требуют больше времени. После этого вы проверяете результат в студии.',
  },
  {
    question: 'Можно проверить ответы и характер общения?',
    answer: 'Да. В студии можно открыть предпросмотр и задать виджету посетительские вопросы. Чат помогает проверить ответы, но не изменяет сам виджет.',
  },
  {
    question: 'Что увидит клиент до публикации?',
    answer: 'Ничего не изменится на вашем сайте, пока вы сами не проверите результат и не решите опубликовать виджет.',
  },
  {
    question: 'Как установить готовый виджет на сайт?',
    answer: 'После публикации Kaigo подготовит короткий код установки. Его можно добавить в настройки сайта или передать вашему разработчику.',
  },
] as const;

export function FaqSection() {
  const campaignStudioHref = studioHref();
  const [openIndex, setOpenIndex] = useState(0);
  const reducedMotion = useReducedMotion();

  return (
    <section className="landing-section faq-section" id="faq" data-landing-section>
      <div className="landing-shell faq-layout">
        <Reveal className="faq-copy">
          <p className="section-kicker">Вопросы и помощь</p>
          <h2>Понятно даже<br />без технического опыта</h2>
          <p>Kaigo ведёт от ссылки до готового виджета. Результат можно проверить в предпросмотре и опубликовать после решения.</p>
          <div className="faq-help">
            <Question size={55} weight="regular" aria-hidden="true" />
            <span><strong>Нужна помощь с первым запуском?</strong><small>Откройте студию или посмотрите пошаговую инструкцию.</small></span>
            <div><a className="primary-button" href={campaignStudioHref}>Перейти в студию</a><a className="secondary-button" href="#how-it-works">Открыть инструкцию</a></div>
          </div>
        </Reveal>

        <div className="faq-list">
          {questions.map(({ question, answer }, index) => {
            const open = openIndex === index;
            const panelId = `faq-answer-${index}`;
            return (
              <Reveal
                className="faq-item__entrance"
                delay={0.06 + index * 0.09}
                key={question}
                preset={index % 2 === 0 ? 'fromLeft' : 'fromRight'}
              >
                <article className="faq-item" data-open={open ? 'true' : 'false'}>
                  <button
                    type="button"
                    aria-expanded={open}
                    aria-controls={panelId}
                    onClick={() => setOpenIndex(open ? -1 : index)}
                  >
                    <span>{index + 1}</span><strong>{question}</strong><CaretDown size={25} weight="bold" aria-hidden="true" />
                  </button>
                  <AnimatePresence initial={false}>
                    {open ? (
                      <motion.div
                        className="faq-item__answer"
                        id={panelId}
                        initial={reducedMotion ? false : { height: 0, opacity: 0 }}
                        animate={{ height: 'auto', opacity: 1 }}
                        exit={reducedMotion ? undefined : { height: 0, opacity: 0 }}
                        transition={reducedMotion ? { duration: 0 } : { type: 'spring', stiffness: 132, damping: 20, mass: 0.82 }}
                      >
                        <p>{answer}</p>{index === 0 ? <LinkSimple size={45} weight="regular" aria-hidden="true" /> : <ArrowRight size={34} aria-hidden="true" />}
                      </motion.div>
                    ) : null}
                  </AnimatePresence>
                </article>
              </Reveal>
            );
          })}
        </div>
      </div>
    </section>
  );
}
