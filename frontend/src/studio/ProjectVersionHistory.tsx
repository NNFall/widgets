import { useState } from 'react';

import type { SaasProjectVersion } from './types';

interface ProjectVersionHistoryProps {
  versions: SaasProjectVersion[];
  activeVersionId: string | null;
  selectedVersionId: string | null;
  mutationPending: boolean;
  running: boolean;
  onSelect: (versionId: string) => void;
  onRestore: (versionId: string) => void;
}

const kindLabel: Record<SaasProjectVersion['kind'], string> = {
  initial: 'Первая версия',
  refinement: 'Доработка',
  restore: 'Восстановленная версия',
};

const dateFormatter = new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
});

function normalizedChangeRequest(value: string) {
  return value.replace(/\s+/g, ' ').trim();
}

export function ProjectVersionHistory({
  versions,
  activeVersionId,
  selectedVersionId,
  mutationPending,
  running,
  onSelect,
  onRestore,
}: ProjectVersionHistoryProps) {
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => new Set());

  const toggleExpanded = (versionId: string) => {
    setExpandedIds((current) => {
      const next = new Set(current);
      if (next.has(versionId)) next.delete(versionId);
      else next.add(versionId);
      return next;
    });
  };

  return (
    <section className="studio-versions" aria-label="История версий">
      <header>
        <strong>История версий</strong>
        <span>{versions.length}</span>
      </header>
      <div>
        {versions.map((version) => {
          const active = version.id === activeVersionId;
          const selected = version.id === selectedVersionId;
          const changeRequest = version.change_request
            ? normalizedChangeRequest(version.change_request)
            : '';
          const expandable = changeRequest.length > 240;
          const expanded = expandedIds.has(version.id);
          const displayedRequest = expandable && !expanded
            ? `${changeRequest.slice(0, 237)}…`
            : changeRequest;
          return (
            <article
              key={version.id}
              className={active ? 'is-active' : selected ? 'is-selected' : undefined}
              data-active={active ? 'true' : undefined}
            >
              <div>
                <strong>Версия {version.ordinal}</strong>
                <span>{kindLabel[version.kind]}</span>
                {active && <span className="studio-versions__active">Текущая версия</span>}
                <time dateTime={version.created_at}>
                  {dateFormatter.format(new Date(version.created_at))}
                </time>
              </div>
              {changeRequest && (
                <div className="studio-versions__request">
                  <p>{displayedRequest}</p>
                  {expandable && (
                    <button
                      type="button"
                      className="studio-versions__expand"
                      aria-expanded={expanded}
                      onClick={() => toggleExpanded(version.id)}
                    >
                      {expanded ? 'Свернуть' : 'Показать полностью'}
                    </button>
                  )}
                </div>
              )}
              <button
                type="button"
                disabled={mutationPending}
                onClick={() => onSelect(version.id)}
                aria-current={selected ? 'true' : undefined}
                aria-label={`Просмотреть версию ${version.ordinal}`}
              >
                {selected ? 'Выбрана' : 'Просмотреть'}
              </button>
              {!active && (
                <button
                  type="button"
                  disabled={running || mutationPending}
                  onClick={() => onRestore(version.id)}
                  aria-label={`Восстановить версию ${version.ordinal}`}
                >
                  Восстановить
                </button>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}
