import { useCallback, useEffect, useRef, useState } from 'react';

import {
  BuilderApiError,
  builderUrl,
  cancelBuilderRun,
  createBuilderRun,
  getBuilderRun,
  refineBuilderRun,
  retryBuilderRun,
} from './api';
import type {
  BuilderEvent,
  BuilderRunInput,
  BuilderRunSnapshot,
  BuilderRunStatus,
  StudioError,
} from './types';

export const ACTIVE_RUN_STORAGE_KEY = 'kaigo.builder.activeRun.v1';
const POLL_INTERVAL_MS = 2_000;

const TERMINAL_STATUSES = new Set<BuilderRunStatus>(['completed', 'failed', 'cancelled']);

const ERROR_MESSAGES: Record<string, string> = {
  missing_api_key: 'Сервис генерации пока не настроен.',
  provider_unavailable: 'Gemini сейчас недоступен. Запуск можно повторить позже.',
  model_unavailable: 'Выбранная модель Gemini сейчас недоступна.',
  quota_exceeded: 'Лимит Gemini временно исчерпан. Попробуйте позже.',
  generation_timeout: 'Генерация заняла слишком много времени и была остановлена.',
  invalid_artifact: 'Полученную версию не удалось безопасно открыть.',
  visual_quality_failed: 'Финальная визуальная проверка не пройдена.',
  visual_review_inconclusive: 'Визуальную проверку не удалось завершить уверенно.',
  reference_capture_failed: 'Не удалось снять главную страницу сайта.',
  reference_capture_incomplete: 'Не удалось полностью снять главную страницу сайта.',
  reference_analysis_failed: 'Не удалось закончить анализ исходного сайта.',
  run_cancelled: 'Генерация отменена.',
  internal_error: 'Во время генерации произошла внутренняя ошибка.',
};

function readStoredRun() {
  try {
    return localStorage.getItem(ACTIVE_RUN_STORAGE_KEY);
  } catch {
    return null;
  }
}

function storeRun(runId: string) {
  try {
    localStorage.setItem(ACTIVE_RUN_STORAGE_KEY, runId);
  } catch {
    // Persistence is an enhancement; the active browser session can continue without it.
  }
}

function forgetRun() {
  try {
    localStorage.removeItem(ACTIVE_RUN_STORAGE_KEY);
  } catch {
    // Storage may be blocked in privacy modes.
  }
}

function describeError(error: unknown, fallback = 'Не удалось выполнить запрос к студии.'): StudioError {
  if (error instanceof BuilderApiError) {
    return {
      message: (error.code && ERROR_MESSAGES[error.code]) || fallback,
      raw: error.raw,
      code: error.code,
    };
  }
  if (error instanceof Error) {
    return { message: fallback, raw: error.message, code: null };
  }
  return { message: fallback, raw: String(error), code: null };
}

function terminalError(code: string | null, raw: string): StudioError {
  return {
    message: (code && ERROR_MESSAGES[code]) || 'Генерация завершилась с ошибкой.',
    raw: raw || code || 'Подробности не переданы',
    code,
  };
}

export interface BuilderRunController {
  runId: string | null;
  snapshot: BuilderRunSnapshot | null;
  events: BuilderEvent[];
  error: StudioError | null;
  activityMessage: string;
  connection: 'idle' | 'streaming' | 'polling';
  isHydrating: boolean;
  createRun: (input: BuilderRunInput) => Promise<void>;
  cancelRun: () => Promise<void>;
  retryRun: () => Promise<void>;
  refineRun: (message: string) => Promise<void>;
  clearError: () => void;
}

export function useBuilderRun(): BuilderRunController {
  const [runId, setRunId] = useState<string | null>(() => readStoredRun());
  const [snapshot, setSnapshot] = useState<BuilderRunSnapshot | null>(null);
  const [events, setEvents] = useState<BuilderEvent[]>([]);
  const [error, setError] = useState<StudioError | null>(null);
  const [activityMessage, setActivityMessage] = useState(
    runId ? 'Восстанавливаем запуск' : 'Ожидает запуска',
  );
  const [connection, setConnection] = useState<'idle' | 'streaming' | 'polling'>('idle');
  const [isHydrating, setIsHydrating] = useState(Boolean(runId));
  const [streamVersion, setStreamVersion] = useState(0);
  const snapshotRef = useRef<BuilderRunSnapshot | null>(null);

  const applySnapshot = useCallback((next: BuilderRunSnapshot) => {
    snapshotRef.current = next;
    setSnapshot(next);
    setIsHydrating(false);
    if (next.status === 'completed') {
      setActivityMessage('Готово — виджет проверен');
      setError(null);
    } else if (next.status === 'cancelled') {
      setActivityMessage('Генерация отменена');
      setError(terminalError('run_cancelled', 'Запуск отменён пользователем'));
    } else if (next.status === 'failed') {
      setActivityMessage('Генерация остановлена');
      setError((current) => current ?? terminalError(next.error_code, next.error_code ?? 'Ошибка генерации'));
    } else if (next.status === 'running') {
      setActivityMessage((current) => (
        current === 'Восстанавливаем запуск' || current === 'Ожидает запуска'
          ? 'Генерация выполняется'
          : current
      ));
    }
  }, []);

  const adoptRun = useCallback((next: BuilderRunSnapshot, message: string) => {
    setEvents([]);
    setError(null);
    setActivityMessage(message);
    setRunId(next.run_id);
    storeRun(next.run_id);
    applySnapshot(next);
    setStreamVersion((version) => version + 1);
  }, [applySnapshot]);

  useEffect(() => {
    if (!runId) {
      setConnection('idle');
      setIsHydrating(false);
      return;
    }

    let disposed = false;
    let stream: EventSource | null = null;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let terminalFromEvent = false;

    const stopPolling = () => {
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = null;
    };

    const refresh = async () => {
      try {
        const next = await getBuilderRun(runId);
        if (disposed) return;
        applySnapshot(next);
        if (TERMINAL_STATUSES.has(next.status)) {
          terminalFromEvent = true;
          stopPolling();
        }
      } catch (caught) {
        if (disposed) return;
        if (caught instanceof BuilderApiError && caught.status === 404) {
          forgetRun();
          setRunId(null);
          setSnapshot(null);
          setEvents([]);
          setError(null);
          setActivityMessage('Ожидает запуска');
          return;
        }
        setIsHydrating(false);
        setError(describeError(caught, 'Не удалось получить состояние запуска.'));
      }
    };

    const startPolling = () => {
      if (disposed || pollTimer || terminalFromEvent || TERMINAL_STATUSES.has(snapshotRef.current?.status ?? 'created')) return;
      setConnection('polling');
      pollTimer = setInterval(refresh, POLL_INTERVAL_MS);
    };

    void refresh();
    try {
      stream = new EventSource(builderUrl(`api/runs/${encodeURIComponent(runId)}/events`));
      setConnection('streaming');
      stream.onmessage = (message) => {
        if (disposed) return;
        try {
          const incoming = JSON.parse(message.data) as BuilderEvent;
          if (incoming.run_id !== runId || !Number.isInteger(incoming.sequence)) return;
          setEvents((current) => {
            if (current.some((item) => item.sequence === incoming.sequence)) return current;
            return [...current, incoming].sort((left, right) => left.sequence - right.sequence);
          });
          setActivityMessage(incoming.message || 'Генерация выполняется');
          if (incoming.type === 'run.failed') {
            terminalFromEvent = true;
            setError(terminalError(incoming.error_code, incoming.message));
          } else if (incoming.type === 'run.cancelled') {
            terminalFromEvent = true;
            setError(terminalError('run_cancelled', incoming.message));
          } else if (incoming.type === 'run.completed') {
            terminalFromEvent = true;
            setError(null);
          }
          void refresh();
        } catch (caught) {
          setError(describeError(caught, 'Получено повреждённое событие генерации.'));
        }
      };
      stream.onerror = () => {
        stream?.close();
        if (terminalFromEvent) setConnection('idle');
        else startPolling();
      };
    } catch {
      startPolling();
    }

    return () => {
      disposed = true;
      stream?.close();
      stopPolling();
    };
  }, [applySnapshot, runId, streamVersion]);

  const createRun = useCallback(async (input: BuilderRunInput) => {
    setError(null);
    setActivityMessage('Создаём запуск');
    try {
      const next = await createBuilderRun(input);
      adoptRun(next, 'Запуск создан');
    } catch (caught) {
      setActivityMessage('Запуск не создан');
      setError(describeError(caught, 'Не удалось создать запуск.'));
    }
  }, [adoptRun]);

  const cancelRun = useCallback(async () => {
    if (!runId) return;
    setError(null);
    try {
      await cancelBuilderRun(runId);
      setActivityMessage('Отмена запрошена');
      const next = await getBuilderRun(runId);
      applySnapshot(next);
    } catch (caught) {
      setError(describeError(caught, 'Не удалось отменить генерацию.'));
    }
  }, [applySnapshot, runId]);

  const retryRun = useCallback(async () => {
    if (!runId) return;
    setError(null);
    setActivityMessage('Повторяем запуск');
    try {
      const next = await retryBuilderRun(runId);
      adoptRun(next, 'Повторный запуск создан');
    } catch (caught) {
      setError(describeError(caught, 'Не удалось повторить запуск.'));
    }
  }, [adoptRun, runId]);

  const refineRun = useCallback(async (message: string) => {
    if (!runId) return;
    setError(null);
    setActivityMessage('Готовим доработку');
    try {
      const next = await refineBuilderRun(runId, message);
      adoptRun(next, 'Доработка начата');
    } catch (caught) {
      setError(describeError(caught, 'Не удалось запустить доработку.'));
    }
  }, [adoptRun, runId]);

  return {
    runId,
    snapshot,
    events,
    error,
    activityMessage,
    connection,
    isHydrating,
    createRun,
    cancelRun,
    retryRun,
    refineRun,
    clearError: () => setError(null),
  };
}
