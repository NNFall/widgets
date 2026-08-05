import { Check } from '@phosphor-icons/react';

import { StudioActivity } from './StudioActivity';
import { runStatusPresentation, STUDIO_STAGES } from './studioPresentation';
import type { BuilderEvent, BuilderRunStatus, BuilderStage } from './types';

type StudioProgressProps = {
  status: BuilderRunStatus | null;
  progress: number;
  currentStage?: BuilderStage | null;
  lastCompletedStage?: BuilderStage | null;
  events: BuilderEvent[];
  activityFallback: string;
};

type StageState = 'completed' | 'current' | 'upcoming';

function stageState(
  index: number,
  status: BuilderRunStatus | null,
  currentIndex: number,
  completedIndex: number,
): StageState {
  if (status === 'completed' || index <= completedIndex) return 'completed';
  if (index === currentIndex) return 'current';
  if (currentIndex < 0 && status === 'running' && index === Math.min(completedIndex + 1, STUDIO_STAGES.length - 1)) {
    return 'current';
  }
  return 'upcoming';
}

export function StudioProgress({
  status,
  progress,
  currentStage,
  lastCompletedStage,
  events,
  activityFallback,
}: StudioProgressProps) {
  const presentation = runStatusPresentation(status);
  const progressValue = Math.max(0, Math.min(100, Math.round(progress)));
  const currentIndex = STUDIO_STAGES.findIndex(({ id }) => id === currentStage);
  const completedIndex = STUDIO_STAGES.findIndex(({ id }) => id === lastCompletedStage);
  const running = status === 'queued' || status === 'running';
  const displayedCurrentIndex = currentIndex >= 0
    ? currentIndex
    : running
      ? Math.min(completedIndex + 1, STUDIO_STAGES.length - 1)
      : -1;

  return (
    <section className="studio-progress-card" aria-labelledby="studio-progress-title">
      <div className="studio-progress-card__heading">
        <div>
          <p className="studio-kicker">Ход создания</p>
          <h2 id="studio-progress-title">{presentation.title}</h2>
          <p>{presentation.detail}</p>
        </div>
        <strong aria-label={`Готово на ${progressValue} процентов`}>{progressValue}%</strong>
      </div>

      <div
        className="studio-progress-card__bar"
        role="progressbar"
        aria-label="Готовность виджета"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={progressValue}
      >
        <span style={{ width: `${progressValue}%` }} />
      </div>

      <ol className="studio-stages" aria-label="Этапы создания виджета">
        {STUDIO_STAGES.map((stage, index) => {
          const state = stageState(index, status, currentIndex, completedIndex);
          return (
            <li
              key={stage.id}
              data-state={state}
              aria-current={state === 'current' ? 'step' : undefined}
            >
              <span className="studio-stages__marker" aria-hidden>
                {state === 'completed' ? <Check size={15} weight="bold" /> : index + 1}
              </span>
              <span>{stage.label}</span>
            </li>
          );
        })}
      </ol>

      {displayedCurrentIndex >= 0 && running && (
        <p className="studio-progress-card__step">
          Этап {displayedCurrentIndex + 1} из {STUDIO_STAGES.length}
        </p>
      )}
      {running && (
        <StudioActivity
          running
          events={events}
          stage={currentStage}
          fallback={activityFallback}
          status={status}
        />
      )}
    </section>
  );
}
