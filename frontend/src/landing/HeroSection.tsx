import { List, X } from '@phosphor-icons/react';
import { useRef, useState } from 'react';

import { KaigoLogo } from '../shared/KaigoLogo';
import { UrlComposer } from '../shared/UrlComposer';
import { HeroOrbitScene } from './HeroOrbitScene';

const navItems = [
  ['Продукт', '#product'],
  ['Как это работает', '#how-it-works'],
  ['Кейсы', '#case-study'],
  ['Вопросы', '#faq'],
] as const;

export function HeroSection() {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuToggleRef = useRef<HTMLButtonElement>(null);

  const closeMobileMenu = () => {
    setMenuOpen(false);
    menuToggleRef.current?.focus({ preventScroll: true });
  };

  return (
    <>
      <header className="site-header">
        <div className="site-header__inner">
          <a className="site-header__logo" href="/" aria-label="Kaigo — главная">
            <KaigoLogo />
          </a>
          <nav className="site-header__nav" aria-label="Основная навигация">
            {navItems.map(([label, href]) => <a href={href} key={href}>{label}</a>)}
          </nav>
          <a className="site-header__cta" href="/studio">Перейти в студию</a>
          <button
            ref={menuToggleRef}
            className="site-header__menu-toggle"
            type="button"
            aria-label={menuOpen ? 'Закрыть меню' : 'Открыть меню'}
            aria-expanded={menuOpen}
            aria-controls="mobile-navigation"
            onClick={() => setMenuOpen((value) => !value)}
          >
            {menuOpen ? <X size={26} aria-hidden="true" /> : <List size={28} aria-hidden="true" />}
          </button>
        </div>
        <nav
          className="site-header__mobile-nav"
          id="mobile-navigation"
          aria-label="Мобильная навигация"
          data-open={menuOpen ? 'true' : 'false'}
          hidden={!menuOpen}
        >
          {navItems.map(([label, href]) => (
            <a href={href} key={href} onClick={closeMobileMenu}>{label}</a>
          ))}
          <a href="/studio" onClick={closeMobileMenu}>Перейти в студию</a>
        </nav>
      </header>

      <section className="hero-section" id="product" data-landing-section>
        <div className="hero-section__inner">
          <div className="hero-copy">
            <h1>
              <span className="hero-title-line">Через 10 минут</span>
              <span className="hero-title-line">вы сможете сказать:</span>
              <span className="hero-title-line">наш бизнес</span>
              <span className="hero-title-line">использует AI</span>
            </h1>
            <p>
              Добавьте ссылку — Kaigo изучит страницы, услуги, стиль и вопросы клиентов,
              а затем создаст персонального AI-консультанта.
            </p>
            <UrlComposer />
          </div>
          <HeroOrbitScene />
        </div>
      </section>
    </>
  );
}
