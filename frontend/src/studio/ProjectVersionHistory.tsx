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

function boundedChangeRequest(value: string) {
  const compact = value.replace(/\s+/g, ' ').trim();
  return compact.length > 240 ? `${compact.slice(0, 237)}…` : compact;
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
          return (
            <article
              key={version.id}
              className={active ? 'is-active' : selected ? 'is-selected' : undefined}
              data-active={active ? 'true' : undefined}
            >
              <div>
                <strong>Версия {version.ordinal}</strong>
                <span>{kindLabel[version.kind]}</span>
                <span>Ревизия артефакта {version.artifact_revision}</span>
                {active && <span className="studio-versions__active">Активная версия</span>}
                <time dateTime={version.created_at}>
                  {dateFormatter.format(new Date(version.created_at))}
                </time>
              </div>
              {version.change_request && <p>{boundedChangeRequest(version.change_request)}</p>}
              <button
                type="button"
                disabled={mutationPending}
                onClick={() => onSelect(version.id)}
                aria-current={selected ? 'true' : undefined}
                aria-label={`Просмотреть версию ${version.ordinal}`}
              >
                {selected ? 'Выбрана' : 'Просмотреть'}
              </button>
              <button
                type="button"
                disabled={active || running || mutationPending}
                onClick={() => onRestore(version.id)}
                aria-label={`Восстановить версию ${version.ordinal}`}
              >
                {active ? 'Активна' : 'Восстановить'}
              </button>
            </article>
          );
        })}
      </div>
    </section>
  );
}
