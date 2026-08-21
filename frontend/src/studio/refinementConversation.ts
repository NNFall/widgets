import type {
  BuilderRunStatus,
  SaasProjectVersion,
  SaasRunSnapshot,
} from './types';
import { safeActivityForEvent } from './studioPresentation';

const COMPLETED_FALLBACK = 'Доработка завершена. Откройте результат справа и проверьте изменения.';
const INTERNAL_ASSIGNMENT = /(?:\b(?:model|provider|prompt|system[_\s-]?prompt|internal[_\s-]?payload|payload|api[_\s-]?key|authorization|traceback)\b\s*[:=]|["'](?:model|provider|prompt|system_prompt|internal_payload|payload|api_key)["']\s*:|\bbearer\s+[a-z0-9._~-]+)/i;
const INTERNAL_TERM = /\b(?:model|provider|prompt|payload|system[_\s-]?prompt|internal[_\s-]?payload)\b|(?:промпт|провайдер|внутренн(?:ий|яя|ее)\s+(?:контекст|сообщение|инструкц|данные))/i;
const INTERNAL_ROLE_OR_INSTRUCTION = /\b(?:system|developer|assistant|user|tool)\s*:|(?:you are (?:the |an? )?(?:provider |system )?assistant|ignore (?:all |the )?previous instructions?|print (?:the )?secrets?|reveal (?:the )?hidden|ты\s+[—-]\s+системный|игнорируй предыдущие инструкции?|системн(?:ый|ая|ое)\s+(?:промпт|сообщение)|внутренн(?:ий|яя|ее)\s+(?:промпт|инструкц))/i;
const TECHNICAL_SYNTAX = /[<>{}`\u0000-\u001f\u007f]|https?:\/\/|www\.|(?:^|\s)[a-z_][a-z0-9_.-]{1,40}\s*=|(?:^|\s)(?:\/root\/|[a-z]:\\)|#[0-9a-f]{3,8}\b/i;
const CYRILLIC_LETTER = /[А-ЯЁа-яё]/g;

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

function safeCompletedSummary(run: SaasRunSnapshot | undefined) {
  const events = run?.events ?? [];
  let rawMessage: string | null | undefined;
  for (let index = events.length - 1; index >= 0; index -= 1) {
    if (events[index]?.type === 'stage.completed') {
      rawMessage = events[index]?.message;
      break;
    }
  }
  const raw = rawMessage?.trim();
  if (!raw || raw.length > 1_000) return null;
  if (
    INTERNAL_ASSIGNMENT.test(raw)
    || INTERNAL_TERM.test(raw)
    || INTERNAL_ROLE_OR_INSTRUCTION.test(raw)
    || TECHNICAL_SYNTAX.test(raw)
  ) return null;
  const message = raw.replace(/\s+/g, ' ');
  if ((message.match(CYRILLIC_LETTER) ?? []).length < 8) return null;
  return message;
}

function safeProgressMessage(run: SaasRunSnapshot | undefined) {
  const event = run?.events?.at(-1);
  if (!event) return null;
  return safeActivityForEvent({
    type: event.type,
    stage: event.payload.stage ?? null,
    message: event.message,
  });
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
      message: safeCompletedSummary(run) ?? COMPLETED_FALLBACK,
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
    message: safeProgressMessage(run)
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
