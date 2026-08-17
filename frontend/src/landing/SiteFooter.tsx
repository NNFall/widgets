import { KaigoLogo } from '../shared/KaigoLogo';
import { marketingHref } from '../shared/marketing';

export function SiteFooter() {
  return (
    <footer className="site-footer" role="contentinfo">
      <div className="landing-shell site-footer__inner">
        <div className="site-footer__brand">
          <a href={marketingHref('/')} aria-label="Kaigo — главная">
            <KaigoLogo tone="coral" />
          </a>
        </div>

        <nav className="site-footer__links" aria-label="Ссылки в подвале">
          <a href={marketingHref('/#contact')}>Связаться с командой</a>
          <a href={marketingHref('/privacy/')}>Политика конфиденциальности</a>
          <a href={marketingHref('/personal-data-consent/')}>Согласие на обработку данных</a>
          <a href={marketingHref('/terms/')}>Условия использования</a>
          <a href={marketingHref('/offer/')}>Предварительная оферта</a>
        </nav>

        <p className="site-footer__meta">© 2026 Kaigo · Самозанятый, плательщик НПД · реквизиты уточняются</p>
      </div>
    </footer>
  );
}
