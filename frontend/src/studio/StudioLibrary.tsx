import { ArrowRight, GlobeSimple, Plus } from '@phosphor-icons/react';
import { useEffect, useState } from 'react';

import { getProjects } from './api';
import { projectStatusLabel } from './studioPresentation';
import type { SaasProject } from './types';

type StudioLibraryProps = {
  onOpenProject: (projectId: string) => void;
  onCreateProject: () => void;
};

type LibraryState =
  | { kind: 'loading' }
  | { kind: 'error' }
  | { kind: 'ready'; projects: SaasProject[] };

function projectDomain(sourceUrl: string) {
  try {
    return new URL(sourceUrl).hostname.replace(/^www\./, '') || 'Сайт проекта';
  } catch {
    return 'Сайт проекта';
  }
}

function projectDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Дата не указана';
  return new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function projectActionLabel(status: string) {
  if (status === 'completed' || status === 'free_result_ready' || status === 'published') return 'Посмотреть';
  if (status === 'running' || status === 'generating' || status === 'queued') return 'Следить';
  if (status === 'failed' || status === 'cancelled') return 'Исправить';
  return 'Открыть';
}

export function StudioLibrary({ onOpenProject, onCreateProject }: StudioLibraryProps) {
  const [requestVersion, setRequestVersion] = useState(0);
  const [state, setState] = useState<LibraryState>({ kind: 'loading' });

  useEffect(() => {
    const abort = new AbortController();
    setState({ kind: 'loading' });
    void getProjects(abort.signal)
      .then(({ projects }) => {
        if (!Array.isArray(projects)) throw new Error('invalid_projects_response');
        setState({ kind: 'ready', projects });
      })
      .catch(() => {
        if (!abort.signal.aborted) setState({ kind: 'error' });
      });
    return () => abort.abort();
  }, [requestVersion]);

  return (
    <section className="studio-library" aria-labelledby="studio-library-title">
      <header className="studio-library__header">
        <div>
          <p className="studio-kicker">Kaigo Studio</p>
          <h1 id="studio-library-title">Мои виджеты</h1>
          <p>Откройте готовый проект или продолжите тот, который ещё создаётся.</p>
        </div>
        <button type="button" className="studio-library__new" onClick={onCreateProject}>
          <Plus aria-hidden size={18} weight="bold" />
          Новый виджет
        </button>
      </header>

      {state.kind === 'loading' && (
        <p className="studio-library__loading" role="status">Загружаем ваши виджеты…</p>
      )}

      {state.kind === 'error' && (
        <div className="studio-library__error" role="alert">
          <div>
            <strong>Не удалось загрузить ваши виджеты</strong>
            <p>Проверьте соединение и повторите попытку.</p>
          </div>
          <button type="button" onClick={() => setRequestVersion((value) => value + 1)}>
            Попробовать ещё раз
          </button>
        </div>
      )}

      {state.kind === 'ready' && state.projects.length === 0 && (
        <div className="studio-library__empty">
          <GlobeSimple aria-hidden size={28} />
          <div>
            <h2>Здесь появятся ваши виджеты</h2>
            <p>Первый проект займёт пару минут: достаточно ссылки на сайт и короткого пожелания.</p>
          </div>
          <button type="button" onClick={onCreateProject}>Создать первый виджет</button>
        </div>
      )}

      {state.kind === 'ready' && state.projects.length > 0 && (
        <div className="studio-library__list">
          {state.projects.map((project) => (
            <article className="studio-library__project" key={project.id}>
              <div className="studio-library__identity">
                <span aria-hidden><GlobeSimple size={18} /></span>
                <div>
                  <h2>{projectDomain(project.source_url)}</h2>
                  {project.brief?.trim() && (
                    <p className="studio-library__brief">{project.brief.trim()}</p>
                  )}
                  {project.owner_email && (
                    <p className="studio-library__owner">Владелец: {project.owner_email}</p>
                  )}
                  <time dateTime={project.updated_at}>{projectDate(project.updated_at)}</time>
                </div>
              </div>
              <span className="studio-library__status" data-status={project.status}>
                {projectStatusLabel(project.status)}
              </span>
              <button type="button" onClick={() => onOpenProject(project.id)}>
                {projectActionLabel(project.status)} <ArrowRight aria-hidden size={17} />
              </button>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
