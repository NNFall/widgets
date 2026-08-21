import type {
  BuilderRunStatus,
  SaasProjectVersion,
  SaasRunSnapshot,
} from './types';

const COMPLETED_FALLBACK = 'Доработка завершена. Откройте результат справа и проверьте изменения.';

export interface PendingRefinementConversation {
  changeRequest: string;
  runId: string | null;
  status: BuilderRunStatus;
  assistantMessage: string | null;
}

export interface RefinementConversationEntry {
  id: string;
  runId: string | null;
  changeRequest: string;
  status: BuilderRunStatus;
  versionNumber: number | null;
  assistantTitle: string;
  assistantMessage: string;
}

function publicStageMessage(run: SaasRunSnapshot | undefined, completed: boolean) {
  const candidates = (run?.events ?? []).filter((event) => (
    event.type === 'stage.completed'
    || (!completed && event.type === 'stage.started')
  ));
  for (let index = candidates.length - 1; index >= 0; index -= 1) {
    const message = candidates[index]?.message?.trim();
    if (message) return message;
  }
  return null;
}

function presentation(
  status: BuilderRunStatus,
  versionNumber: number | null,
  run: SaasRunSnapshot | undefined,
  pendingMessage: string | null = null,
) {
  if (status === 'completed') {
    return {
      title: versionNumber ? `Готово — версия ${versionNumber}` : 'Доработка готова',
      message: publicStageMessage(run, true) ?? COMPLETED_FALLBACK,
    };
  }
  if (status === 'failed') {
    return {
      title: 'Не удалось завершить доработку',
      message: pendingMessage ?? 'Последняя сохранённая версия не потеряна. Запрос можно повторить.',
    };
  }
  if (status === 'cancelled') {
    return {
      title: 'Доработка остановлена',
      message: 'Последняя сохранённая версия не потеряна. Запрос можно запустить снова.',
    };
  }
  return {
    title: 'Доработка выполняется',
    message: publicStageMessage(run, false)
      ?? pendingMessage
      ?? (status === 'running' ? 'Kaigo вносит изменения и проверяет результат.' : 'Доработка поставлена в очередь'),
  };
}

export function buildRefinementConversation(
  versions: SaasProjectVersion[],
  runsById: Readonly<Record<string, SaasRunSnapshot>>,
  pending: PendingRefinementConversation | null,
): RefinementConversationEntry[] {
  const entries: RefinementConversationEntry[] = versions
    .filter((version) => version.kind === 'refinement' && Boolean(version.change_request?.trim()))
    .sort((left, right) => left.ordinal - right.ordinal)
    .map((version) => {
      const run = runsById[version.run_id];
      const status = run?.status ?? 'completed';
      const copy = presentation(status, version.ordinal, run);
      return {
        id: version.id,
        runId: version.run_id,
        changeRequest: version.change_request!.trim(),
        status,
        versionNumber: version.ordinal,
        assistantTitle: copy.title,
        assistantMessage: copy.message,
      } satisfies RefinementConversationEntry;
    });

  if (pending && !entries.some((entry) => entry.runId && entry.runId === pending.runId)) {
    const run = pending.runId ? runsById[pending.runId] : undefined;
    const status = run?.status ?? pending.status;
    const copy = presentation(status, null, run, pending.assistantMessage);
    entries.push({
      id: pending.runId ?? 'pending-refinement',
      runId: pending.runId,
      changeRequest: pending.changeRequest,
      status,
      versionNumber: null,
      assistantTitle: copy.title,
      assistantMessage: copy.message,
    });
  }

  return entries;
}
