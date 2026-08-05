import { CloudArrowUp, Clock, Eye, LinkSimple, PaperPlaneTilt } from '@phosphor-icons/react';
import type { FormEvent } from 'react';

interface StudioComposerProps {
  title?: string;
  description?: string;
  submitLabel?: string;
  headingLevel?: 'h1' | 'h2';
  sourceUrl: string;
  brief: string;
  pending: boolean;
  error: string | null;
  onSourceUrlChange: (value: string) => void;
  onBriefChange: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}

const journey = [
  {
    title: 'Добавьте сайт',
    copy: 'Нужна только публичная ссылка.',
    Icon: LinkSimple,
  },
  {
    title: 'Проверьте виджет',
    copy: 'Посмотрите результат и задайте вопросы.',
    Icon: Eye,
  },
  {
    title: 'Опубликуйте',
    copy: 'Подключите только когда всё устраивает.',
    Icon: CloudArrowUp,
  },
] as const;

export function StudioComposer({
  title = 'Создайте первый AI-виджет',
  description = 'Сначала получите рабочий результат бесплатно. Публикация и подключение готового виджета доступны по тарифу.',
  submitLabel = 'Создать AI-виджет',
  headingLevel = 'h1',
  sourceUrl,
  brief,
  pending,
  error,
  onSourceUrlChange,
  onBriefChange,
  onSubmit,
}: StudioComposerProps) {
  const Heading = headingLevel;

  return (
    <section className="studio-composer" aria-labelledby="studio-composer-title">
      <div className="studio-composer__card">
        <p className="studio-kicker">Бесплатная экспресс-версия</p>
        <Heading id="studio-composer-title">{title}</Heading>
        <p>{description}</p>
        <ol className="studio-composer__journey" aria-label="Путь до запуска">
          {journey.map(({ title: stepTitle, copy, Icon }, index) => (
            <li key={stepTitle}>
              <span aria-hidden><Icon size={18} weight="bold" /></span>
              <div>
                <strong>{stepTitle}</strong>
                <small>{copy}</small>
              </div>
              <i aria-hidden>{index + 1}</i>
            </li>
          ))}
        </ol>
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
            {submitLabel}
          </button>
        </form>
      </div>
    </section>
  );
}
