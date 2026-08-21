import { describe, expect, it } from 'vitest';

import { buildRefinementConversation } from './refinementConversation';
import type { SaasProjectVersion, SaasRunSnapshot } from './types';

const version: SaasProjectVersion = {
  id: 'version-2',
  project_id: 'project-1',
  ordinal: 2,
  kind: 'refinement',
  change_request: 'Сделай кнопку заметнее',
  parent_version_id: 'version-1',
  run_id: 'run-2',
  artifact_id: 'artifact-2',
  artifact_revision: 2,
  refinable: true,
  created_at: '2026-08-21T12:00:00Z',
};

function run(overrides: Partial<SaasRunSnapshot>): SaasRunSnapshot {
  return {
    id: 'run-2',
    project_id: 'project-1',
    mode: 'express',
    status: 'completed',
    progress: 100,
    current_stage: null,
    last_completed_stage: 'validation',
    error_code: null,
    error_message: null,
    created_at: '2026-08-21T12:00:00Z',
    started_at: '2026-08-21T12:00:01Z',
    finished_at: '2026-08-21T12:09:00Z',
    latest_sequence: 0,
    ...overrides,
  };
}

describe('refinement conversation', () => {
  it('uses a clear completed fallback when no public summary was persisted', () => {
    const [entry] = buildRefinementConversation([version], {
      'run-2': run({ events: [] }),
    }, null);

    expect(entry).toMatchObject({
      changeRequest: 'Сделай кнопку заметнее',
      assistantTitle: 'Готово — версия 2',
      assistantMessage: 'Доработка завершена. Откройте результат справа и проверьте изменения.',
    });
  });

  it('ties cancellation to the request without exposing the raw run error', () => {
    const [entry] = buildRefinementConversation([], {
      'run-cancelled': run({
        id: 'run-cancelled',
        status: 'cancelled',
        error_message: 'provider prompt and internal payload',
      }),
    }, {
      changeRequest: 'Верни спокойную анимацию',
      runId: 'run-cancelled',
      status: 'cancelled',
      assistantMessage: null,
    });

    expect(entry).toMatchObject({
      changeRequest: 'Верни спокойную анимацию',
      assistantTitle: 'Доработка остановлена',
    });
    expect(JSON.stringify(entry)).not.toContain('provider prompt');
    expect(JSON.stringify(entry)).not.toContain('internal payload');
  });
});

