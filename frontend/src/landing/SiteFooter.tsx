import { KaigoLogo } from '../shared/KaigoLogo';
import { CONTACT_CONFIG } from '../shared/contact';
import { studioHref } from '../shared/campaign';
import { marketingHref } from '../shared/marketing';

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="landing-shell site-footer__inner">
        <div className="site-footer__brand">
          <a href={marketingHref('/')} aria-label="Kaigo — главная">
            <KaigoLogo tone="coral" />
          </a>
          <p>Персональные AI-виджеты<br />для бизнеса.</p>
        </div>

        <nav className="site-footer__nav" aria-label="Навигация в подвале">
          <a href={marketingHref('/#product')}>Продукт</a>
          <a href={marketingHref('/#product-tour')}>Как это работает</a>
          <a href={marketingHref('/#case-study')}>Кейсы</a>
          <a href={marketingHref('/#faq')}>Помощь</a>
          <a href={marketingHref('/#contact')}>Связаться с командой</a>
          <a href={marketingHref(studioHref())}>Студия</a>
        </nav>

        <nav className="site-footer__legal" aria-label="Правовая информация">
          <a href={marketingHref('/privacy/')}>Политика конфиденциальности</a>
          <a href={marketingHref('/personal-data-consent/')}>Согласие на обработку данных</a>
          <a href={marketingHref('/terms/')}>Условия использования</a>
          <a href={marketingHref('/offer/')}>Предварительная оферта</a>
        </nav>

        <div className="site-footer__operator" data-preview-placeholder>
          <span>Реквизиты для предпросмотра</span>
          <p>Оператор: уточняется до подтверждения<br />Статус (подтверждено владельцем): {CONTACT_CONFIG.operatorStatus}<br />ФИО: уточняется<br />ИНН: уточняется<br />Адрес: уточняется</p>
          <a href={`mailto:${encodeURIComponent(CONTACT_CONFIG.supportEmail)}`}>{CONTACT_CONFIG.supportEmail}</a>
        </div>
      </div>
    </footer>
  );
}
