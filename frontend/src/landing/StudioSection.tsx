import { ChatCircleDots, CheckCircle, Eye, Plug, UploadSimple } from '@phosphor-icons/react';

import { BrowserMockup } from '../shared/BrowserMockup';
import { useMotionActivity } from '../shared/MotionActivity';
import { Reveal } from '../shared/Reveal';
import { studioHref } from '../shared/campaign';

const verificationSteps = [
  {
    title: 'Предпросмотр',
    copy: 'Оцените готовую экспресс-версию до оплаты.',
    Icon: Eye,
  },
  {
    title: 'Проверка диалога',
    copy: 'Задайте виджету посетительские вопросы в чате.',
    Icon: ChatCircleDots,
  },
  {
    title: 'Публикация и подключение',
    copy: 'Разместите проверенный виджет на сайте по тарифу.',
    Icon: Plug,
  },
] as const;

export function StudioSection() {
  const campaignStudioHref = studioHref();
  const { active, reducedMotion, ref } = useMotionActivity<HTMLElement>();

  return (
    <section
      className="landing-section studio-section"
      id="studio-showcase"
      data-landing-section
      data-motion-active={active ? 'true' : 'false'}
      ref={ref}
    >
      <div className="landing-shell studio-layout">
        <Reveal className="studio-copy">
          <p className="section-kicker">Студия Kaigo</p>
          <h2 aria-label="Готовый вариант — под вашим контролем"><span>Готовый вариант —</span><span>под вашим контролем</span></h2>
          <p>Откройте результат в студии, проверьте внешний вид и ответы в чате. Публикация и подключение к сайту доступны только после вашего решения.</p>
          <a className="primary-button primary-button--wide" href={campaignStudioHref}>Перейти в студию</a>
          <span className="studio-copy__note"><CheckCircle size={22} />Публикация только после вашей проверки</span>
        </Reveal>

        <Reveal className="studio-demo__entrance" delay={0.08} preset="scale">
          <div className="studio-demo">
            <div className="studio-demo__sidebar">
              <h3>Перед публикацией</h3>
              <p>Три шага с готовым результатом</p>
              <ol className="studio-demo__checklist">
                {verificationSteps.map(({ title, copy, Icon }) => (
                  <li key={title}>
                    <Icon size={21} aria-hidden="true" />
                    <span><strong>{title}</strong><small>{copy}</small></span>
                  </li>
                ))}
              </ol>
            </div>
            <div className="studio-demo__workspace">
              <div className="studio-demo__toolbar">
                <span className="studio-demo__version">Готовая версия</span>
                <span className="studio-demo__action studio-demo__preview-label"><Eye size={18} />Предпросмотр</span>
                <span className="studio-demo__action studio-demo__publish-label"><UploadSimple size={18} />Опубликовать</span>
              </div>
              <div className="studio-demo__preview">
                <BrowserMockup widgetVisible motionComplete motionActive={active} reducedMotion={reducedMotion} testIds={false} siteVariant="ceramics" />
              </div>
            </div>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
