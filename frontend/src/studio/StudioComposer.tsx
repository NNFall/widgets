import { Clock, PaperPlaneTilt } from '@phosphor-icons/react';
import type { FormEvent } from 'react';

interface StudioComposerProps {
  sourceUrl: string;
  brief: string;
  pending: boolean;
  error: string | null;
  onSourceUrlChange: (value: string) => void;
  onBriefChange: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}

export function StudioComposer({
  sourceUrl,
  brief,
  pending,
  error,
  onSourceUrlChange,
  onBriefChange,
  onSubmit,
}: StudioComposerProps) {
  return (
    <section className="studio-composer" aria-labelledby="studio-composer-title">
      <div className="studio-composer__card">
        <p className="studio-kicker">Бесплатная экспресс-версия</p>
        <h1 id="studio-composer-title">Создайте первый AI-виджет</h1>
        <p>Сначала получите рабочий результат бесплатно. Публикация и подключение готового виджета доступны по тарифу.</p>
        <form onSubmit={onSubmit}>
          <label htmlFor="saas-studio-url">Ссылка на сайт</label>
          <input
            id="saas-studio-url"
            type="url"
            value={sourceUrl}
            onChange={(event) => onSourceUrlChange(event.target.value)}
          />
          <label htmlFor="saas-studio-brief">Пожелание к AI-виджету</label>
          <textarea
            id="saas-studio-brief"
            maxLength={4_000}
            placeholder="Необязательно: стиль ответов, цель или важные ограничения"
            value={brief}
            onChange={(event) => onBriefChange(event.target.value)}
          />
          <small className="studio-composer__saved">Проверьте оба поля: после первого запуска исходные данные фиксируются.</small>
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
