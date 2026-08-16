import { useEffect } from 'react';
import { ArrowLeft } from '@phosphor-icons/react';

import { CONTACT_CONFIG, supportMailtoHref } from '../shared/contact';
import { KaigoLogo } from '../shared/KaigoLogo';
import { marketingHref } from '../shared/marketing';
import { SiteFooter } from '../landing/SiteFooter';
import type { LegalDocument } from './legalDocuments';
import { previewReminder } from './legalDocuments';

type LegalPageProps = {
  document: LegalDocument;
};

export function LegalPage({ document }: LegalPageProps) {
  useEffect(() => {
    const previousTitle = window.document.title;
    window.document.title = `Kaigo — ${document.title}`;

    return () => {
      window.document.title = previousTitle;
    };
  }, [document.title]);

  return (
    <main className="legal-page">
      <header className="legal-header">
        <div className="legal-shell legal-header__inner">
          <a className="legal-back" href={marketingHref('/')}>
            <ArrowLeft size={20} aria-hidden="true" />
            Вернуться на главную
          </a>
          <a className="legal-header__logo" href={marketingHref('/')} aria-label="Kaigo — главная">
            <KaigoLogo tone="coral" />
          </a>
        </div>
      </header>

      <div className="legal-shell legal-layout">
        <article className="legal-document">
          <div className="legal-notice" role="note">
            <strong>Тестовая редакция</strong>
            <p>{previewReminder}</p>
          </div>
          <p className="legal-document__kicker">Kaigo · Правовая информация</p>
          <h1>{document.title}</h1>
          <p className="legal-document__lead">{document.lead}</p>
          <p className="legal-document__revision">Редакция для предпросмотра. Дата вступления в силу уточняется.</p>

          <nav className="legal-contents" aria-label="Содержание документа">
            <strong>Содержание</strong>
            <ol>
              {document.sections.map((section) => (
                <li key={section.id}><a href={`#${section.id}`}>{section.title}</a></li>
              ))}
              <li><a href="#legal-contact">Контакт и статус реквизитов</a></li>
            </ol>
          </nav>

          <div className="legal-document__sections">
            {document.sections.map((section) => (
              <section className="legal-section" id={section.id} key={section.id}>
                <h2>{section.title}</h2>
                {section.content}
              </section>
            ))}
          </div>

          <section className="legal-contact" id="legal-contact" aria-labelledby="legal-contact-title">
            <h2 id="legal-contact-title">Контакт и статус реквизитов</h2>
            <p>Почта для вопросов: <a href={supportMailtoHref()}>{CONTACT_CONFIG.supportEmail}</a>. Это предварительный адрес для предпросмотра, его приём должен подтвердить владелец.</p>
            <p>Оператор: уточняется до подтверждения. Статус, указанный владельцем: {CONTACT_CONFIG.operatorStatus}. ФИО: уточняется. ИНН и адрес: уточняются. Telegram пока не опубликован.</p>
          </section>
        </article>
      </div>

      <SiteFooter />
    </main>
  );
}
