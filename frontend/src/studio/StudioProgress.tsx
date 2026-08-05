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
  if (
    currentIndex < 0
    && status === 'running'
    && index === Math.min(completedIndex + 1, STUDIO_STAGES.length - 1)
  ) {
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
  const live = status === 'queued' || status === 'running';
  const working = status === 'running';
  const displayedCurrentIndex = currentIndex >= 0
    ? currentIndex
    : working
      ? Math.min(completedIndex + 1, STUDIO_STAGES.length - 1)
      : -1;
  const displayedStage = displayedCurrentIndex >= 0 ? STUDIO_STAGES[displayedCurrentIndex] : null;
  const hasStagePresentation = live && displayedStage !== null;
  const kicker = hasStagePresentation
    ? `Сейчас идёт этап ${displayedCurrentIndex + 1} из ${STUDIO_STAGES.length}`
    : status === 'completed'
      ? 'Результат'
      : status === 'failed' || status === 'cancelled'
        ? 'Требуется действие'
        : 'Ход создания';
  const heading = hasStagePresentation ? displayedStage.label : presentation.title;
  const detail = hasStagePresentation ? displayedStage.activity : presentation.detail;

  return (
    <section className="studio-progress-card" aria-labelledby="studio-progress-title" data-status={status ?? 'idle'}>
      <div className="studio-progress-card__heading">
        <div>
          <p className="studio-kicker">{kicker}</p>
          <h2 id="studio-progress-title">{heading}</h2>
          <p>{detail}</p>
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

      {live && (
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
