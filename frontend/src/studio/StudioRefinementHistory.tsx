import type { RefinementConversationEntry } from './refinementConversation';

type StudioRefinementHistoryProps = {
  entries: RefinementConversationEntry[];
};

export function StudioRefinementHistory({ entries }: StudioRefinementHistoryProps) {
  if (entries.length === 0) return null;

  return (
    <ol className="studio-refinement-history" aria-label="История доработок">
      {entries.map((entry) => {
        const isActive = entry.status === 'queued' || entry.status === 'running';
        return (
          <li className="studio-refinement-turn" key={entry.id} data-status={entry.status}>
            <div className="studio-message studio-message--user">
              <span className="studio-message__author">Вы</span>
              <p>{entry.changeRequest}</p>
            </div>
            <div
              className="studio-message studio-message--assistant studio-message--refinement"
              role={isActive ? 'status' : undefined}
              aria-live={isActive ? 'polite' : undefined}
              aria-atomic={isActive ? 'true' : undefined}
            >
              <span className="studio-message__mark" aria-hidden>K</span>
              <div>
                <strong>{entry.assistantTitle}</strong>
                <p>{entry.assistantMessage}</p>
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
