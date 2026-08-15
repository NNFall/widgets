import { CheckCircle, List, X } from '@phosphor-icons/react';
import { useCallback, useEffect, useRef, useState } from 'react';

import { KaigoLogo } from '../shared/KaigoLogo';
import { UrlComposer } from '../shared/UrlComposer';
import { studioHref } from '../shared/campaign';
import { HeroOrbitScene } from './HeroOrbitScene';

const navItems = [
  ['Продукт', '#product'],
  ['Как это работает', '#product-tour'],
  ['Кейсы', '#case-study'],
  ['Вопросы', '#faq'],
] as const;

export function HeroSection() {
  const campaignStudioHref = studioHref();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuToggleRef = useRef<HTMLButtonElement>(null);
  const focusFrameRef = useRef<number | null>(null);

  useEffect(() => () => {
    if (focusFrameRef.current !== null) {
      window.cancelAnimationFrame(focusFrameRef.current);
    }
  }, []);

  const closeMobileMenu = useCallback(() => {
    setMenuOpen(false);
    if (focusFrameRef.current !== null) {
      window.cancelAnimationFrame(focusFrameRef.current);
    }
    focusFrameRef.current = window.requestAnimationFrame(() => {
      focusFrameRef.current = null;
      menuToggleRef.current?.focus({ preventScroll: true });
    });
  }, []);

  useEffect(() => {
    if (!menuOpen) return;

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      closeMobileMenu();
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [closeMobileMenu, menuOpen]);

  return (
    <>
      <header className="site-header">
        <div className="site-header__inner">
          <a className="site-header__logo" href="/" aria-label="Kaigo — главная">
            <KaigoLogo tone="coral" />
          </a>
          <nav className="site-header__nav" aria-label="Основная навигация">
            {navItems.map(([label, href]) => <a href={href} key={href}>{label}</a>)}
          </nav>
          <a className="site-header__cta" href={campaignStudioHref}>Перейти в студию</a>
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
          <a href={campaignStudioHref} onClick={closeMobileMenu}>Перейти в студию</a>
        </nav>
      </header>

      <section className="hero-section" id="product" data-landing-section>
        <div className="hero-section__inner">
          <div className="hero-copy">
            <h1 aria-label="Через 10 минут вы сможете сказать: наш бизнес использует AI">
              <span className="hero-title-line">Через 10 минут</span>
              <span className="hero-title-line">вы сможете сказать:</span>
              <span className="hero-title-line">наш бизнес</span>
              <span className="hero-title-line">использует AI</span>
            </h1>
            <p>
              Добавьте ссылку — Kaigo бесплатно создаст первую версию AI-виджета:
              изучит страницы, услуги, стиль и вопросы клиентов, а затем соберёт
              персонального AI-консультанта для вашего бизнеса.
            </p>
            <div className="hero-proof">
              <CheckCircle size={26} weight="fill" aria-hidden="true" />
              <span><strong>Сначала посмотрите результат и проверьте ответы.</strong> Оплата нужна только перед публикацией.</span>
            </div>
            <UrlComposer />
          </div>
          <HeroOrbitScene />
        </div>
      </section>
    </>
  );
}
