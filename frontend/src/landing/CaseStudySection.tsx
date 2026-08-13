import { CheckCircle, ShieldCheck, Sparkle } from '@phosphor-icons/react';
import { motion } from 'motion/react';
import { useCallback, useState, useSyncExternalStore } from 'react';

import { BrowserMockup } from '../shared/BrowserMockup';
import { useMotionActivity } from '../shared/MotionActivity';
import { Reveal } from '../shared/Reveal';
import { studioHref } from '../shared/campaign';

export const MOBILE_CASE_MEDIA_QUERY = '(max-width: 767px)';

export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback((onStoreChange: () => void) => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return () => undefined;

    const mediaQuery = window.matchMedia(query);
    const handleChange = () => onStoreChange();

    if (typeof mediaQuery.addEventListener === 'function') {
      mediaQuery.addEventListener('change', handleChange);
      return () => mediaQuery.removeEventListener('change', handleChange);
    }

    mediaQuery.addListener(handleChange);
    return () => mediaQuery.removeListener(handleChange);
  }, [query]);

  const getSnapshot = useCallback(
    () => typeof window !== 'undefined'
      && typeof window.matchMedia === 'function'
      && window.matchMedia(query).matches,
    [query],
  );
  const getServerSnapshot = useCallback(() => false, []);

  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

export function CaseStudySection() {
  const campaignStudioHref = studioHref();
  const [activeView, setActiveView] = useState<'before' | 'after'>('after');
  const { active, reducedMotion, ref } = useMotionActivity<HTMLElement>();
  const mobileComparison = useMediaQuery(MOBILE_CASE_MEDIA_QUERY);
  const beforeInactive = mobileComparison && activeView !== 'before';
  const afterInactive = mobileComparison && activeView !== 'after';

  return (
    <section
      className="landing-section case-section"
      id="case-study"
      data-landing-section
      data-case-view={activeView}
      data-motion-active={active ? 'true' : 'false'}
      ref={ref}
    >
      <div className="landing-shell">
        <Reveal className="case-heading">
          <div className="case-heading__copy">
            <p className="section-kicker section-kicker--pill">Кейс: строительная компания</p>
            <h2>Что меняется для посетителя сайта</h2>
          </div>
          <div className="case-heading__outcome">
            <span>Результат после Kaigo</span>
            <strong>Не нужно искать ответ по разделам — можно просто спросить</strong>
            <p>AI-консультант остаётся внутри сайта, отвечает по найденной информации и помогает перейти к подходящему проекту или заявке.</p>
          </div>
        </Reveal>

        <div className="case-toggle" role="group" aria-label="Показать состояние сайта">
          <button type="button" aria-pressed={activeView === 'before'} onClick={() => setActiveView('before')}>До</button>
          <button type="button" aria-pressed={activeView === 'after'} onClick={() => setActiveView('after')}>После</button>
        </div>

        <div className="case-comparison">
          <Reveal className="case-panel-shell">
            <div
              className={`case-panel case-panel--before${activeView === 'before' ? ' is-mobile-active is-active' : ''}`}
              aria-hidden={beforeInactive || undefined}
              inert={beforeInactive || undefined}
            >
              <div className="case-panel__label"><span>Без AI-консультанта</span><p>Посетитель сам открывает проекты, цены и условия, а затем ищет способ связаться.</p></div>
              <div className="case-browser"><BrowserMockup widgetVisible={false} motionComplete={false} motionActive={false} reducedMotion testIds={false} /></div>
            </div>
          </Reveal>
          <Reveal className="case-panel-shell" delay={0.08}>
            <div
              className={`case-panel case-panel--after${activeView === 'after' ? ' is-mobile-active is-active' : ''}`}
              aria-hidden={afterInactive || undefined}
              inert={afterInactive || undefined}
            >
              <div className="case-panel__label"><span>С AI-консультантом</span><p>Посетитель задаёт вопрос на той же странице и сразу получает понятный следующий шаг.</p></div>
              <div className="case-browser" data-case-widget="enhanced" data-widget-placement="embedded">
                <motion.div
                  className="case-browser__after-reveal"
                  key={activeView === 'after' ? 'case-after-active' : 'case-after-idle'}
                  initial={activeView === 'after' && !reducedMotion ? { opacity: 0.72, y: 42, scale: 0.88, rotate: -2.5 } : false}
                  animate={{ opacity: 1, y: 0, scale: 1, rotate: 0 }}
                  transition={reducedMotion ? { duration: 0 } : { type: 'spring', stiffness: 148, damping: 13, mass: 0.82 }}
                >
                  <span className="case-after-halo" aria-hidden="true" />
                  <BrowserMockup widgetVisible motionComplete={active} motionActive={active} reducedMotion={reducedMotion} testIds={false} />
                </motion.div>
              </div>
            </div>
          </Reveal>
        </div>

        <Reveal className="case-footer">
          <span><CheckCircle size={25} />Знает услуги</span>
          <span><ShieldCheck size={25} />Сохраняет стиль сайта</span>
          <span><Sparkle size={25} />Помогает сделать следующий шаг</span>
          <div className="case-footer__action" data-testid="case-action">
            <a className="primary-button" href={campaignStudioHref}>Создать виджет для своего сайта</a>
            <small>Сначала получите бесплатный предпросмотр</small>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
