import { EnvelopeSimple, PaperPlaneTilt, Question } from '@phosphor-icons/react';

import { FeedbackComposer } from '../shared/FeedbackComposer';
import { CONTACT_CONFIG, supportMailtoHref } from '../shared/contact';

type StudioContactPanelProps = {
  domain?: string | null;
  projectId?: string | null;
};

type StudioHelpButtonProps = {
  compact?: boolean;
  onOpen: () => void;
};

function safeContextValue(value: string | null | undefined) {
  return value?.trim().replace(/[\r\n]+/g, ' ') ?? '';
}

function studioContactContext({ domain, projectId }: StudioContactPanelProps) {
  const context = [
    safeContextValue(domain) ? `Домен: ${safeContextValue(domain)}` : '',
    safeContextValue(projectId) ? `ID проекта: ${safeContextValue(projectId)}` : '',
  ].filter(Boolean);

  return context.length > 0 ? context.join('\n') : undefined;
}

export function StudioHelpButton({ compact = false, onOpen }: StudioHelpButtonProps) {
  return (
    <button
      type="button"
      className={`studio-help-button${compact ? ' studio-help-button--compact' : ''}`}
      onClick={onOpen}
      aria-label={compact ? 'Помощь' : 'Помощь и обратная связь'}
    >
      <Question aria-hidden size={18} weight="bold" />
      <span>{compact ? 'Помощь' : 'Помощь и обратная связь'}</span>
    </button>
  );
}

export function StudioContactPanel({ domain, projectId }: StudioContactPanelProps) {
  const context = studioContactContext({ domain, projectId });

  return (
    <section className="studio-contact-panel" aria-label="Помощь и обратная связь">
      <div className="studio-contact-panel__intro">
        <p className="studio-kicker">Связь с Kaigo</p>
        <h3>Помощь и обратная связь</h3>
        <p>Расскажите, что получилось или где нужна помощь. Команда прочитает обращение и ответит по почте.</p>
      </div>

      <div className="studio-contact-panel__channels" aria-label="Каналы связи">
        <div className="studio-contact-panel__channel">
          <span>Почта</span>
          <div>
            <a href={supportMailtoHref()}>{CONTACT_CONFIG.supportEmail}</a>
            <small>Откроется ваше почтовое приложение. Письмо отправится только после вашего подтверждения.</small>
          </div>
          <EnvelopeSimple aria-hidden size={19} />
        </div>
        <div className="studio-contact-panel__channel studio-contact-panel__telegram">
          <span>Telegram</span>
          <div>
            <strong>Добавим после подтверждения контакта</strong>
            <small>Публичная ссылка пока не опубликована.</small>
          </div>
          <PaperPlaneTilt aria-hidden size={19} />
        </div>
      </div>

      <FeedbackComposer
        page="/studio"
        studioContext={context}
        className="studio-contact-panel__composer"
      />
    </section>
  );
}
