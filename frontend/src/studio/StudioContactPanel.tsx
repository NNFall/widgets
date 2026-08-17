import { Question } from '@phosphor-icons/react';

import { FeedbackComposer } from '../shared/FeedbackComposer';

type StudioContactPanelProps = {
  csrfToken?: string | null;
};

type StudioHelpButtonProps = {
  compact?: boolean;
  onOpen: () => void;
};

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

export function StudioContactPanel({ csrfToken = null }: StudioContactPanelProps) {
  return (
    <section className="studio-contact-panel" aria-label="Помощь и обратная связь">
      <div className="studio-contact-panel__intro">
        <p className="studio-kicker">Связь с Kaigo</p>
        <h3>Помощь и обратная связь</h3>
        <p>Вопрос, ошибка, идея или предложение о сотрудничестве сохранится в Kaigo. Контактные данные не нужны — просто напишите сообщение.</p>
      </div>

      <FeedbackComposer
        source="studio_account"
        csrfToken={csrfToken}
        className="studio-contact-panel__composer"
      />
    </section>
  );
}
