import { Clock, PaperPlaneTilt } from '@phosphor-icons/react';
import type { FormEvent } from 'react';

interface StudioComposerProps {
  sourceUrl: string;
  brief: string;
  sourceLocked: boolean;
  pending: boolean;
  error: string | null;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}

export function StudioComposer({
  sourceUrl,
  brief,
  sourceLocked,
  pending,
  error,
  onSubmit,
}: StudioComposerProps) {
  return (
    <section className="studio-composer" aria-labelledby="studio-composer-title">
      <div className="studio-composer__card">
        <p className="studio-kicker">Бесплатная экспресс-версия</p>
        <h1 id="studio-composer-title">Создайте первый AI-виджет</h1>
        <p>Сначала получите рабочий результат бесплатно. Доработка и публикация потребуют тариф позже.</p>
        <form onSubmit={onSubmit}>
          <label htmlFor="saas-studio-url">Ссылка на сайт</label>
          <input
            id="saas-studio-url"
            type="url"
            value={sourceUrl}
            readOnly={sourceLocked}
            aria-readonly={sourceLocked}
          />
          <p className="studio-composer__summary-label" id="saas-studio-brief-label">
            Сохранённое пожелание к AI-виджету
          </p>
          <div
            className="studio-composer__summary"
            role="textbox"
            aria-readonly="true"
            aria-labelledby="saas-studio-brief-label"
          >
            {brief || 'Без дополнительного пожелания'}
          </div>
          <small className="studio-composer__saved">Сохранено в проекте и будет использовано для этого запуска.</small>
          {error && <p className="studio-form__error" role="alert">{error}</p>}
          <button type="submit" className="studio-create" disabled={pending}>
            {pending ? <Clock aria-hidden size={20} /> : <PaperPlaneTilt aria-hidden size={20} weight="fill" />}
            Создать AI-виджет
          </button>
        </form>
      </div>
    </section>
  );
}
