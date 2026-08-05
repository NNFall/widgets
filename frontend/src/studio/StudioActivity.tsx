import { useReducedMotion } from 'motion/react';
import { useEffect, useRef, useState } from 'react';

import { safeActivityForEvent, STUDIO_STAGES } from './studioPresentation';
import type { BuilderEvent, BuilderStage } from './types';

const MIN_VISIBLE_MS = 2_000;

type StudioActivityProps = {
  running: boolean;
  events: BuilderEvent[];
  stage?: BuilderStage | null;
  fallback: string;
};

type ActivityItem = {
  key: string;
  text: string;
};

function activityItem(events: BuilderEvent[], stage: BuilderStage | null | undefined, fallback: string): ActivityItem {
  const latest = events.at(-1);
  if (latest) {
    return {
      key: `${latest.run_id}-${latest.sequence}`,
      text: safeActivityForEvent(latest),
    };
  }

  const stageActivity = STUDIO_STAGES.find(({ id }) => id === stage)?.activity;
  const text = fallback || stageActivity || 'Готовы начать работу';
  return { key: `fallback-${stage ?? 'idle'}-${text}`, text };
}

export function StudioActivity({ running, events, stage, fallback }: StudioActivityProps) {
  const reducedMotion = Boolean(useReducedMotion());
  const candidate = activityItem(events, stage, fallback);
  const [visible, setVisible] = useState<ActivityItem>(candidate);
  const visibleAtRef = useRef(Date.now());
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
    if (candidate.key === visible.key) return undefined;

    const showCandidate = () => {
      setVisible(candidate);
      visibleAtRef.current = Date.now();
      timeoutRef.current = null;
    };

    if (!running) {
      showCandidate();
      return undefined;
    }

    const elapsed = Date.now() - visibleAtRef.current;
    timeoutRef.current = setTimeout(showCandidate, Math.max(0, MIN_VISIBLE_MS - elapsed));

    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
    };
  }, [candidate.key, candidate.text, running, visible.key]);

  return (
    <p
      className="studio-activity"
      role="status"
      aria-live="polite"
      aria-atomic="true"
      data-motion={reducedMotion ? 'reduced' : 'allowed'}
    >
      <span className="studio-activity__message" key={visible.key}>{visible.text}</span>
    </p>
  );
}
