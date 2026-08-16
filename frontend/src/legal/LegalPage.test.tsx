import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import stylesSource from '../styles.css?raw';
import mobileStylesSource from '../mobile/foundations.css?raw';
import { LegalPage } from './LegalPage';
import { LEGAL_DOCUMENTS } from './legalDocuments';

afterEach(() => {
  cleanup();
  document.head.querySelectorAll('style[data-test-legal-styles]').forEach((style) => style.remove());
  document.title = '';
  window.history.replaceState({}, '', '/');
});

describe('legal information architecture', () => {
  it.each([
    ['privacy', 'Политика конфиденциальности'],
    ['personal-data-consent', 'Согласие на обработку персональных данных'],
    ['terms', 'Условия использования'],
    ['offer', 'Предварительная публичная оферта'],
  ] as const)('renders the %s document with shared preview structure', (documentId, title) => {
    render(<LegalPage document={LEGAL_DOCUMENTS[documentId]} />);

    expect(screen.getByRole('heading', { name: title, level: 1 })).toBeVisible();
    expect(screen.getByText('Тестовая редакция')).toBeVisible();
    expect(screen.getByText(/Редакция для предпросмотра/i)).toBeVisible();
    expect(screen.getByRole('link', { name: 'Вернуться на главную' })).toHaveAttribute('href', '/');
    expect(screen.getByRole('navigation', { name: 'Содержание документа' })).toBeInTheDocument();
    expect(screen.getByRole('contentinfo')).toBeInTheDocument();
    expect(screen.getAllByText(/support@kaigo\.space/).length).toBeGreaterThan(0);
  });

  it('keeps uncertain operator and backend facts visibly marked instead of inventing requisites', () => {
    render(<LegalPage document={LEGAL_DOCUMENTS.privacy} />);

    const main = screen.getByRole('main');
    expect(within(main).getAllByText(/уточняется до подтверждения/i).length).toBeGreaterThan(0);
    expect(within(main).getByText(/Срок хранения и точный маршрут данных уточняются/i)).toBeVisible();
    expect(within(main).queryByText(/уведомил Роскомнадзор/i)).not.toBeInTheDocument();
  });

  it('uses the dark coral text color for small interactive links', () => {
    expect(stylesSource).toMatch(
      /\.contact-channel\s*>\s*a\s*\{[^}]*color:\s*var\(--coral-text,\s*#a83212\);/s,
    );
    expect(stylesSource).toMatch(
      /\.feedback-composer__consent a,\s*\.feedback-composer__support a\s*\{[^}]*color:\s*var\(--coral-text,\s*#a83212\);/s,
    );
    expect(stylesSource).toMatch(
      /\.site-footer__operator a\s*\{[^}]*color:\s*var\(--coral-text,\s*#a83212\);/s,
    );
    expect(stylesSource).toMatch(
      /\.site-footer__nav a:hover,\s*\.site-footer__legal a:hover,\s*\.site-footer__operator a:hover\s*\{[^}]*color:\s*var\(--coral-text,\s*#a83212\);/s,
    );
    expect(stylesSource).toMatch(
      /\.studio-contact-panel__channel\s*>\s*span\s*\{[^}]*color:\s*#657681;/s,
    );
    expect(stylesSource).not.toMatch(
      /\.site-footer__nav a:hover,\s*\.site-footer__legal a:hover,\s*\.site-footer__operator a:hover\s*\{[^}]*color:\s*var\(--landing-accent/s,
    );
  });

  it.each(['privacy', 'personal-data-consent', 'terms', 'offer'] as const)(
    'keeps %s legal copy understandable without internal English jargon',
    (documentId) => {
      render(<LegalPage document={LEGAL_DOCUMENTS[documentId]} />);

      expect(screen.getByRole('main').textContent ?? '').not.toMatch(
        /\b(?:backend|production|billing|mail-only|provisional)\b/i,
      );
    },
  );

  it('keeps the legal page navigation inside an isolated archive preview', () => {
    window.history.replaceState({}, '', '/frontend/v11/privacy/');
    render(<LegalPage document={LEGAL_DOCUMENTS.privacy} />);

    expect(screen.getByRole('link', { name: 'Вернуться на главную' })).toHaveAttribute('href', '/frontend/v11/');
    expect(screen.getByRole('link', { name: '1. Какие данные могут поступить' })).toHaveAttribute(
      'href',
      '#privacy-data',
    );
  });

  it('uses an explicit effective-date placeholder instead of inventing a legal date', () => {
    render(<LegalPage document={LEGAL_DOCUMENTS.privacy} />);

    expect(screen.getByText(/дата вступления в силу уточняется/i)).toBeVisible();
    expect(screen.queryByText(/15 августа 2026/i)).not.toBeInTheDocument();
  });

  it('sets a meaningful document title and restores the previous title on unmount', () => {
    document.title = 'Marketing preview';
    const { unmount } = render(<LegalPage document={LEGAL_DOCUMENTS.terms} />);

    expect(document.title).toBe('Kaigo — Условия использования');
    unmount();
    expect(document.title).toBe('Marketing preview');
  });

  it('keeps the legal support mail link at a 44px touch target', () => {
    const style = document.createElement('style');
    style.dataset.testLegalStyles = 'true';
    style.textContent = stylesSource;
    document.head.append(style);
    render(<LegalPage document={LEGAL_DOCUMENTS.privacy} />);

    const contact = document.getElementById('legal-contact') as HTMLElement;
    const mailLink = within(contact).getByRole('link', { name: 'support@kaigo.space' });
    expect(getComputedStyle(mailLink).minHeight).toBe('44px');
    expect(mailLink).toHaveAttribute('href', 'mailto:support@kaigo.space');
    expect(mailLink).not.toHaveAttribute('href', expect.stringContaining('%40'));
  });

  it('keeps the footer brand link at a 44px touch target without scaling the logo', () => {
    const style = document.createElement('style');
    style.dataset.testLegalStyles = 'true';
    style.textContent = stylesSource;
    document.head.append(style);
    render(<LegalPage document={LEGAL_DOCUMENTS.privacy} />);

    const logoLink = document.querySelector('.site-footer__brand > a') as HTMLAnchorElement;
    const computed = getComputedStyle(logoLink);
    expect(computed.minHeight).toBe('44px');
    expect(computed.display).toBe('inline-flex');
    expect(stylesSource).toMatch(
      /\.site-footer__brand\s*>\s*a\s*\{[^}]*min-height:\s*44px;[^}]*display:\s*inline-flex;[^}]*align-items:\s*center;/s,
    );
  });

  it('keeps the legal heading wrap-safe at narrow mobile widths', () => {
    const style = document.createElement('style');
    style.dataset.testLegalStyles = 'true';
    style.textContent = stylesSource;
    document.head.append(style);
    render(<LegalPage document={LEGAL_DOCUMENTS.privacy} />);

    const heading = screen.getByRole('heading', { name: 'Политика конфиденциальности', level: 1 });
    const computed = getComputedStyle(heading);
    expect(computed.hyphens).toBe('none');
    expect(computed.overflowWrap).toBe('break-word');
    expect(computed.minWidth).toBe('0px');
    expect(stylesSource).toMatch(
      /\.legal-document h1\s*\{[^}]*min-width:\s*0;[^}]*hyphens:\s*none;[^}]*overflow-wrap:\s*break-word;/s,
    );
  });

  it('uses a readable minimum mobile size for long legal headings', () => {
    expect(mobileStylesSource).toMatch(
      /@media\s+\(max-width:\s*767px\)[\s\S]*?\.legal-document h1\s*\{[^}]*font-size:\s*clamp\(28px,\s*8\.75vw,\s*48px\);/s,
    );
  });

  it('keeps legal mobile navigation and footer text readable', () => {
    expect(mobileStylesSource).toMatch(
      /\.legal-back\s*\{[^}]*font-size:\s*14px;/s,
    );
    expect(stylesSource).toMatch(
      /\.legal-contents\s*>\s*strong\s*\{[^}]*font-size:\s*14px;/s,
    );
    expect(mobileStylesSource).toMatch(
      /\.landing-page \.site-footer__nav a,\s*\.landing-page \.site-footer__legal a,\s*\.legal-page \.site-footer__nav a,\s*\.legal-page \.site-footer__legal a\s*\{[^}]*font-size:\s*14px;/s,
    );
    expect(stylesSource).toMatch(
      /\.site-footer__operator\s*>\s*span\s*\{[^}]*font-size:\s*12px;/s,
    );
    expect(stylesSource).toMatch(
      /\.site-footer__operator p\s*\{[^}]*font-size:\s*14px;/s,
    );
    expect(stylesSource).toMatch(
      /\.site-footer__operator a\s*\{[^}]*font-size:\s*14px;/s,
    );
  });

  it('uses a human-readable legal information kicker', () => {
    render(<LegalPage document={LEGAL_DOCUMENTS['personal-data-consent']} />);

    expect(screen.getByText('Kaigo · Правовая информация')).toBeVisible();
    expect(screen.queryByText(/PERSONAL-DATA-CONSENT/i)).not.toBeInTheDocument();
  });

  it('keeps every legal contents link as a wrapping 44px touch target', () => {
    const style = document.createElement('style');
    style.dataset.testLegalStyles = 'true';
    style.textContent = stylesSource;
    document.head.append(style);
    render(<LegalPage document={LEGAL_DOCUMENTS.privacy} />);

    const contents = document.querySelector('.legal-contents') as HTMLElement;
    const links = within(contents).getAllByRole('link');
    expect(links.length).toBeGreaterThan(1);
    links.forEach((link) => {
      const computed = getComputedStyle(link);
      expect(computed.minHeight).toBe('44px');
      expect(computed.display).toBe('flex');
      expect(computed.whiteSpace).toBe('normal');
      expect(computed.overflowWrap).toBe('anywhere');
    });
    expect(stylesSource).toMatch(
      /\.legal-contents a\s*\{[^}]*min-height:\s*44px;[^}]*display:\s*flex;[^}]*overflow-wrap:\s*anywhere;/s,
    );
  });

  it('keeps privacy categories, purposes, rights and unconfirmed operator fields explicit', () => {
    render(<LegalPage document={LEGAL_DOCUMENTS.privacy} />);

    const main = screen.getByRole('main');
    expect(main).toHaveTextContent(/имя, адрес для ответа и текст сообщения/i);
    expect(main).toHaveTextContent(/идентификаторы аккаунта, проекта и ссылка на сайт/i);
    expect(main).toHaveTextContent(/технические и защитные журналы/i);
    expect(main).toHaveTextContent(/для ответа на обращение.*защиты от злоупотреблений.*улучшения продукта/i);
    expect(main).toHaveTextContent(/исправление или удаление.*отозвать согласие/i);
    expect(main).toHaveTextContent(/Оператор: уточняется до подтверждения/i);
    expect(main).toHaveTextContent(/Статус, указанный владельцем: самозанятый, плательщик НПД/i);
    expect(main).not.toHaveTextContent(/подтверждено владельцем/i);
    expect(main).toHaveTextContent(/ИНН и адрес: уточняются/i);
  });

  it('keeps separate-consent data, actions, duration and withdrawal language explicit', () => {
    render(<LegalPage document={LEGAL_DOCUMENTS['personal-data-consent']} />);

    const main = screen.getByRole('main');
    expect(main).toHaveTextContent(/имени или контакту.*тексту обращения/i);
    expect(main).toHaveTextContent(/получение, запись, систематизация, использование/i);
    expect(main).toHaveTextContent(/срок действия согласия и срок хранения/i);
    expect(main).toHaveTextContent(/отзыва или запроса на удаление/i);
    expect(main).toHaveTextContent(/не должно использоваться как рабочее согласие/i);
    expect(main).not.toHaveTextContent(/оферта для рабочей версии/i);
    expect(main).toHaveTextContent(/не записывает согласие на сервере/i);
  });

  it('keeps terms preview, account, AI, publication, acceptable-use, IP, availability and support clauses explicit', () => {
    render(<LegalPage document={LEGAL_DOCUMENTS.terms} />);

    const main = screen.getByRole('main');
    expect(main).toHaveTextContent(/бесплатн.*предпросмотр.*не обещает точность/i);
    expect(main).toHaveTextContent(/данных аккаунта.*сохранность доступа/i);
    expect(main).toHaveTextContent(/AI-ответы могут быть неполными или ошибочными/i);
    expect(main).toHaveTextContent(/перед публикацией.*проверяет.*отвечает за законность и точность/i);
    expect(main).toHaveTextContent(/Нельзя использовать сервис для незаконных действий/i);
    expect(main).toHaveTextContent(/Права на материалы пользователя остаются у пользователя/i);
    expect(main).toHaveTextContent(/Предпросмотр может изменяться, приостанавливаться или удаляться/i);
    expect(main).toHaveTextContent(/Вопросы можно направить/i);
  });

  it('keeps the preliminary offer blockers and receipt boundary explicit', () => {
    render(<LegalPage document={LEGAL_DOCUMENTS.offer} />);

    const main = screen.getByRole('main');
    expect(main).toHaveTextContent(/Бесплатная генерация и предпросмотр не являются акцептом платной оферты/i);
    expect(main).toHaveTextContent(/цену, состав услуги, срок, порядок отмены и возврата/i);
    expect(main).toHaveTextContent(/реквизиты оператора и способ акцепта/i);
    expect(main).toHaveTextContent(/чеком плательщика НПД/i);
    expect(main).toHaveTextContent(/Платёжный провайдер.*подтверждены до запуска/i);
  });
});
