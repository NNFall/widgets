import { safeActivityForEvent } from './studioPresentation';
import type { BuilderEvent } from './types';

const STAGE_LABELS: Record<string, string> = {
  reference_analysis: 'Анализ сайта',
  persona: 'Выбор сотрудника',
  art_direction: 'Арт-направление',
  composition: 'Подбор шаблонов',
  foundation: 'Основа виджета',
  identity: 'Фирменный стиль',
  conversation: 'Диалог',
  motion_polish: 'Анимации и отделка',
  validation: 'Техническая проверка',
  agent_build: 'Агентская сборка',
};

function eventTime(timestamp: string) {
  const value = new Date(timestamp);
  if (Number.isNaN(value.getTime())) return 'время не указано';
  return new Intl.DateTimeFormat('ru-RU', {
    hour: '2-digit',
    minute: '2-digit',
  }).format(value);
}

function eventCountLabel(count: number) {
  const mod100 = count % 100;
  const mod10 = count % 10;
  if (mod100 >= 11 && mod100 <= 14) return `${count} событий`;
  if (mod10 === 1) return `${count} событие`;
  if (mod10 >= 2 && mod10 <= 4) return `${count} события`;
  return `${count} событий`;
}

export function StudioTimeline({ events, running }: { events: BuilderEvent[]; running: boolean }) {
  const visibleEvents = events.slice(-8);

  return (
    <details className="studio-technical" data-running={running ? 'true' : 'false'}>
      <summary>
        <span>Технические детали</span>
        <small>{events.length > 0 ? eventCountLabel(events.length) : 'пока нет событий'}</small>
      </summary>
      <div className="studio-technical__body">
        <p>Краткая история выполнения — без внутренних данных и служебных сообщений.</p>
        {visibleEvents.length === 0 ? (
          <p className="studio-technical__empty">Детали появятся после запуска создания.</p>
        ) : (
          <ol>
            {visibleEvents.map((event) => (
              <li key={`${event.run_id}-${event.sequence}`}>
                <strong>{safeActivityForEvent(event)}</strong>
                <span>
                  шаг {event.sequence} · <time dateTime={event.timestamp}>{eventTime(event.timestamp)}</time>
                </span>
                {event.stage && <small>{STAGE_LABELS[event.stage]}</small>}
              </li>
            ))}
          </ol>
        )}
      </div>
    </details>
  );
}
