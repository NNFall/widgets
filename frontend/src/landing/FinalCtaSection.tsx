import { Code, Eye, ShieldCheck } from '@phosphor-icons/react';

import { KaigoLogo } from '../shared/KaigoLogo';
import { Reveal } from '../shared/Reveal';
import { UrlComposer } from '../shared/UrlComposer';

const guarantees = [
  { label: 'Без кода', Icon: Code },
  { label: 'Сначала предпросмотр', Icon: Eye },
  { label: 'Публикация после проверки', Icon: ShieldCheck },
] as const;

export function FinalCtaSection() {
  return (
    <section className="landing-section final-cta-section" id="final-cta" data-landing-section>
      <div className="landing-shell final-cta-content">
        <Reveal className="final-cta-copy">
          <p className="section-kicker">Можно начать прямо сейчас</p>
          <h2>Через 10 минут ваш бизнес<br />сможет использовать AI</h2>
          <p>Вставьте сайт, получите первый вариант и решите, что изменить перед публикацией.</p>
        </Reveal>
        <Reveal className="final-cta-visual" delay={0.08}>
          <div className="final-site-card final-site-card--before"><MiniSite after={false} /></div>
          <div className="final-site-card final-site-card--after"><MiniSite after /></div>
        </Reveal>
        <Reveal className="final-composer" delay={0.12}>
          <UrlComposer
            ariaLabel="Ссылка на сайт — финальная форма"
            submitAriaLabel="Создать AI-виджет по нижней форме"
          />
        </Reveal>
        <Reveal className="guarantee-row">
          {guarantees.map(({ label, Icon }) => <span key={label}><Icon size={30} weight="regular" />{label}</span>)}
        </Reveal>
      </div>
      <footer className="site-footer">
        <div className="landing-shell site-footer__inner">
          <div><KaigoLogo /><p>Персональные AI-виджеты<br />для бизнеса.</p></div>
          <nav aria-label="Навигация в подвале">
            <a href="#product">Продукт</a><a href="#how-it-works">Как это работает</a><a href="#case-study">Кейсы</a><a href="#faq">Помощь</a><a href="/studio">Студия</a>
          </nav>
          <div className="site-footer__legal"><span>Политика конфиденциальности</span><span>Условия использования</span></div>
        </div>
      </footer>
    </section>
  );
}

function MiniSite({ after }: { after: boolean }) {
  return (
    <div className="mini-site" aria-hidden="true">
      <div className="mini-site__chrome"><i /><i /><i /></div>
      <div className="mini-site__nav"><strong>Modern House</strong><span>Проекты</span><span>Услуги</span><span>Контакты</span></div>
      <div className="mini-site__main"><p>Строим дома,<br />в которых хочется<br />жить</p><img src="/assets/house-cutout.png" alt="" /></div>
      {after ? <div className="mini-site__widget"><i />Я изучил ваш сайт.<br />Чем помочь?<b>›</b></div> : null}
    </div>
  );
}
