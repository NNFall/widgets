import { useCallback, useEffect, useRef, useState } from 'react';

import {
  BuilderApiError,
  builderUrl,
  cancelBuilderRun,
  cancelProjectRun,
  createProjectRun,
  createBuilderRun,
  getAuthSession,
  getBuilderRun,
  getProject,
  getProjectRun,
  refineBuilderRun,
  retryBuilderRun,
  retryProjectRun,
  streamProjectRunEvents,
  updateProjectDraft,
} from './api';
import { russianErrorMessage, russianRequestErrorMessage, safeEventMessage } from './errors';
import type {
  BuilderEvent,
  BuilderRunInput,
  BuilderRunSnapshot,
  BuilderRunStatus,
  SaasEvent,
  SaasProject,
  SaasRunSnapshot,
  StudioError,
  TokenUsage,
  WidgetArtifact,
} from './types';

export const ACTIVE_RUN_STORAGE_KEY = 'kaigo.builder.activeRun.v1';
const POLL_INTERVAL_MS = 2_000;
const POLL_MAX_INTERVAL_MS = 30_000;

const TERMINAL_STATUSES = new Set<BuilderRunStatus>(['completed', 'failed', 'cancelled']);
const EMPTY_USAGE: TokenUsage = { prompt_tokens: 0, output_tokens: 0, thinking_tokens: 0, total_tokens: 0 };

interface SnapshotWatermark {
  runId: string;
  latestSequence: number;
  isTerminal: boolean;
  artifactRevision: number;
  updatedAt: number;
}

function snapshotWatermark(snapshot: BuilderRunSnapshot): SnapshotWatermark {
  const updatedAt = Date.parse(snapshot.updated_at);
  return {
    runId: snapshot.run_id,
    latestSequence: snapshot.latest_sequence,
    isTerminal: TERMINAL_STATUSES.has(snapshot.status),
    artifactRevision: Math.max(
      snapshot.artifact?.revision ?? 0,
      snapshot.draft_artifact?.revision ?? 0,
    ),
    updatedAt: Number.isNaN(updatedAt) ? 0 : updatedAt,
  };
}

function isSnapshotRegression(current: SnapshotWatermark, next: SnapshotWatermark) {
  if (current.runId !== next.runId) return false;
  if (next.latestSequence !== current.latestSequence) {
    return next.latestSequence < current.latestSequence;
  }
  if (current.isTerminal && !next.isTerminal) return true;
  if (next.artifactRevision < current.artifactRevision) return true;
  return next.updatedAt < current.updatedAt;
}

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
      message: russianRequestErrorMessage(error.status, error.code, fallback),
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
    message: russianErrorMessage(code, 'Генерация завершилась с ошибкой.'),
    raw: raw || code || 'Подробности не переданы',
    code,
  };
}

export interface BuilderRunController {
  project: SaasProject | null;
  projectMode: boolean;
  csrfToken: string | null;
  runId: string | null;
  snapshot: BuilderRunSnapshot | null;
  events: BuilderEvent[];
  error: StudioError | null;
  activityMessage: string;
  connection: 'idle' | 'streaming' | 'polling';
  isHydrating: boolean;
  mutationPending: boolean;
  createRun: (input: BuilderRunInput) => Promise<void>;
  cancelRun: () => Promise<void>;
  retryRun: () => Promise<void>;
  refineRun: (message: string) => Promise<void>;
  clearError: () => void;
}

function useLegacyBuilderRun(enabled: boolean): BuilderRunController {
  const [runId, setRunId] = useState<string | null>(() => enabled ? readStoredRun() : null);
  const [snapshot, setSnapshot] = useState<BuilderRunSnapshot | null>(null);
  const [events, setEvents] = useState<BuilderEvent[]>([]);
  const [error, setError] = useState<StudioError | null>(null);
  const [activityMessage, setActivityMessage] = useState(
    runId ? 'Восстанавливаем запуск' : 'Ожидает запуска',
  );
  const [connection, setConnection] = useState<'idle' | 'streaming' | 'polling'>('idle');
  const [isHydrating, setIsHydrating] = useState(Boolean(runId));
  const [mutationPending, setMutationPending] = useState(false);
  const [streamVersion, setStreamVersion] = useState(0);
  const activeRunRef = useRef<string | null>(runId);
  const activeRunEpochRef = useRef(0);
  const operationEpochRef = useRef(0);
  const mutationPendingRef = useRef(false);
  const snapshotRef = useRef<BuilderRunSnapshot | null>(null);
  const snapshotWatermarkRef = useRef<SnapshotWatermark | null>(null);

  const beginMutation = useCallback(() => {
    if (mutationPendingRef.current) return null;
    mutationPendingRef.current = true;
    setMutationPending(true);
    operationEpochRef.current += 1;
    return operationEpochRef.current;
  }, []);

  const finishMutation = useCallback((operationEpoch: number) => {
    if (operationEpochRef.current !== operationEpoch) return;
    mutationPendingRef.current = false;
    setMutationPending(false);
    setStreamVersion((version) => version + 1);
  }, []);

  const isCurrentOperation = useCallback((operationEpoch: number, expectedRunId: string | null) => (
    operationEpochRef.current === operationEpoch
    && activeRunRef.current === expectedRunId
  ), []);

  const applySnapshot = useCallback((next: BuilderRunSnapshot) => {
    if (activeRunRef.current !== next.run_id) return false;
    const nextWatermark = snapshotWatermark(next);
    const currentWatermark = snapshotWatermarkRef.current;
    if (currentWatermark && isSnapshotRegression(currentWatermark, nextWatermark)) {
      return false;
    }
    snapshotWatermarkRef.current = nextWatermark;
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
    return true;
  }, []);

  const adoptRun = useCallback((next: BuilderRunSnapshot, message: string) => {
    activeRunEpochRef.current += 1;
    operationEpochRef.current += 1;
    activeRunRef.current = next.run_id;
    mutationPendingRef.current = false;
    setMutationPending(false);
    snapshotWatermarkRef.current = null;
    snapshotRef.current = null;
    setEvents([]);
    setError(null);
    setActivityMessage(message);
    setRunId(next.run_id);
    storeRun(next.run_id);
    applySnapshot(next);
    setStreamVersion((version) => version + 1);
  }, [applySnapshot]);

  useEffect(() => {
    if (!enabled) return;
    if (!runId) {
      activeRunRef.current = null;
      snapshotWatermarkRef.current = null;
      snapshotRef.current = null;
      setConnection('idle');
      setIsHydrating(false);
      return;
    }
    if (snapshotWatermarkRef.current?.runId !== runId) {
      snapshotWatermarkRef.current = null;
      snapshotRef.current = null;
    }
    activeRunRef.current = runId;
    const effectEpoch = activeRunEpochRef.current;
    const effectOperationEpoch = operationEpochRef.current;

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
        if (
          disposed
          || activeRunRef.current !== runId
          || activeRunEpochRef.current !== effectEpoch
          || operationEpochRef.current !== effectOperationEpoch
        ) return;
        if (!applySnapshot(next)) return;
        if (TERMINAL_STATUSES.has(next.status)) {
          terminalFromEvent = true;
          stopPolling();
        }
      } catch (caught) {
        if (
          disposed
          || activeRunRef.current !== runId
          || activeRunEpochRef.current !== effectEpoch
          || operationEpochRef.current !== effectOperationEpoch
        ) return;
        if (caught instanceof BuilderApiError && caught.status === 404) {
          forgetRun();
          activeRunEpochRef.current += 1;
          operationEpochRef.current += 1;
          activeRunRef.current = null;
          mutationPendingRef.current = false;
          setMutationPending(false);
          setRunId(null);
          snapshotWatermarkRef.current = null;
          snapshotRef.current = null;
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
      if (
        disposed
        || activeRunRef.current !== runId
        || activeRunEpochRef.current !== effectEpoch
        || operationEpochRef.current !== effectOperationEpoch
        || pollTimer
        || terminalFromEvent
        || TERMINAL_STATUSES.has(snapshotRef.current?.status ?? 'created')
      ) return;
      setConnection('polling');
      pollTimer = setInterval(refresh, POLL_INTERVAL_MS);
    };

    void refresh();
    try {
      stream = new EventSource(builderUrl(`api/runs/${encodeURIComponent(runId)}/events`));
      setConnection('streaming');
      stream.onmessage = (message) => {
        if (
          disposed
          || activeRunRef.current !== runId
          || activeRunEpochRef.current !== effectEpoch
          || operationEpochRef.current !== effectOperationEpoch
        ) return;
        try {
          const incoming = JSON.parse(message.data) as BuilderEvent;
          if (incoming.run_id !== runId || !Number.isInteger(incoming.sequence)) return;
          setEvents((current) => {
            if (current.some((item) => item.sequence === incoming.sequence)) return current;
            return [...current, incoming].sort((left, right) => left.sequence - right.sequence);
          });
          setActivityMessage(safeEventMessage(incoming) || 'Генерация выполняется');
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
        if (
          disposed
          || activeRunRef.current !== runId
          || activeRunEpochRef.current !== effectEpoch
          || operationEpochRef.current !== effectOperationEpoch
        ) return;
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
  }, [applySnapshot, enabled, runId, streamVersion]);

  const createRun = useCallback(async (input: BuilderRunInput) => {
    const expectedRunId = activeRunRef.current;
    const operationEpoch = beginMutation();
    if (operationEpoch === null) return;
    setError(null);
    setActivityMessage('Создаём запуск');
    try {
      const next = await createBuilderRun(input);
      if (!isCurrentOperation(operationEpoch, expectedRunId)) return;
      adoptRun(next, 'Запуск создан');
    } catch (caught) {
      if (!isCurrentOperation(operationEpoch, expectedRunId)) return;
      setActivityMessage('Запуск не создан');
      setError(describeError(caught, 'Не удалось создать запуск.'));
    } finally {
      finishMutation(operationEpoch);
    }
  }, [adoptRun, beginMutation, finishMutation, isCurrentOperation]);

  const cancelRun = useCallback(async () => {
    const targetRunId = activeRunRef.current;
    if (!targetRunId) return;
    const operationEpoch = beginMutation();
    if (operationEpoch === null) return;
    setError(null);
    try {
      await cancelBuilderRun(targetRunId);
      if (!isCurrentOperation(operationEpoch, targetRunId)) return;
      setActivityMessage('Отмена запрошена');
      const next = await getBuilderRun(targetRunId);
      if (!isCurrentOperation(operationEpoch, targetRunId)) return;
      applySnapshot(next);
    } catch (caught) {
      if (!isCurrentOperation(operationEpoch, targetRunId)) return;
      setError(describeError(caught, 'Не удалось отменить генерацию.'));
    } finally {
      finishMutation(operationEpoch);
    }
  }, [applySnapshot, beginMutation, finishMutation, isCurrentOperation]);

  const retryRun = useCallback(async () => {
    const targetRunId = activeRunRef.current;
    if (!targetRunId) return;
    const operationEpoch = beginMutation();
    if (operationEpoch === null) return;
    setError(null);
    setActivityMessage('Повторяем запуск');
    try {
      const next = await retryBuilderRun(targetRunId);
      if (!isCurrentOperation(operationEpoch, targetRunId)) return;
      adoptRun(next, 'Повторный запуск создан');
    } catch (caught) {
      if (!isCurrentOperation(operationEpoch, targetRunId)) return;
      setError(describeError(caught, 'Не удалось повторить запуск.'));
    } finally {
      finishMutation(operationEpoch);
    }
  }, [adoptRun, beginMutation, finishMutation, isCurrentOperation]);

  const refineRun = useCallback(async (message: string) => {
    const targetRunId = activeRunRef.current;
    if (!targetRunId) return;
    const operationEpoch = beginMutation();
    if (operationEpoch === null) return;
    setError(null);
    setActivityMessage('Готовим доработку');
    try {
      const next = await refineBuilderRun(targetRunId, message);
      if (!isCurrentOperation(operationEpoch, targetRunId)) return;
      adoptRun(next, 'Доработка начата');
    } catch (caught) {
      if (!isCurrentOperation(operationEpoch, targetRunId)) return;
      setError(describeError(caught, 'Не удалось запустить доработку.'));
    } finally {
      finishMutation(operationEpoch);
    }
  }, [adoptRun, beginMutation, finishMutation, isCurrentOperation]);

  return {
    project: null,
    projectMode: false,
    csrfToken: null,
    runId,
    snapshot,
    events,
    error,
    activityMessage,
    connection,
    isHydrating,
    mutationPending,
    createRun,
    cancelRun,
    retryRun,
    refineRun,
    clearError: () => setError(null),
  };
}

function saasStatus(run: SaasRunSnapshot): BuilderRunStatus {
  const status = run.status ?? run.state;
  return status === 'queued' || status === 'running' || status === 'completed'
    || status === 'failed' || status === 'cancelled' || status === 'created'
    ? status
    : 'failed';
}

function adaptSaasEvent(runId: string, event: SaasEvent): BuilderEvent {
  const payload = event.payload ?? {};
  const message = event.type === 'run.created' && event.message === 'Generation queued'
    ? 'Запуск поставлен в очередь'
    : event.message ?? '';
  return {
    run_id: runId,
    sequence: event.sequence,
    timestamp: event.created_at ?? '',
    type: event.type,
    stage: payload.stage ?? null,
    status: payload.status ?? (event.type.endsWith('.failed') ? 'failed' : 'running'),
    message,
    revision: payload.revision ?? null,
    usage: { ...EMPTY_USAGE, ...(payload.usage ?? {}) },
    issues: payload.issues ?? [],
    changes: payload.changes ?? [],
    error_code: payload.error_code ?? null,
  };
}

function aggregateSaasUsage(events: SaasEvent[]): TokenUsage {
  const usage = events.reduce((total, event) => {
    const delta = event.payload?.usage;
    if (!delta) return total;
    const value = (candidate: number | undefined) => Number.isFinite(candidate) ? Math.max(0, candidate ?? 0) : 0;
    total.prompt_tokens += value(delta.prompt_tokens);
    total.output_tokens += value(delta.output_tokens);
    total.thinking_tokens += value(delta.thinking_tokens);
    total.total_tokens += value(delta.total_tokens);
    return total;
  }, { ...EMPTY_USAGE });
  const componentTotal = usage.prompt_tokens + usage.output_tokens + usage.thinking_tokens;
  if (componentTotal > 0) usage.total_tokens = componentTotal;
  return usage;
}

function adaptPreview(preview: SaasRunSnapshot['preview']): WidgetArtifact | null {
  if (!preview) return null;
  return {
    id: preview.id,
    quality_status: preview.quality_status,
    source: preview.source,
    schema_version: preview.schema_version ?? '1',
    revision: preview.revision,
    stage: preview.stage ?? 'validation',
    art_direction: preview.art_direction ?? '',
    body_html: preview.body_html,
    css: preview.css,
    javascript: preview.javascript,
    theme_tokens: preview.theme_tokens ?? {},
    suggested_actions: preview.suggested_actions ?? [],
    change_summary: preview.change_summary ?? '',
    layout_contract: preview.layout_contract ?? {},
  };
}

function adaptSaasRun(project: SaasProject, run: SaasRunSnapshot): BuilderRunSnapshot {
  const preview = adaptPreview(run.preview);
  const status = saasStatus(run);
  const createdAt = Date.parse(run.created_at);
  const finishedAt = Date.parse(run.finished_at ?? new Date().toISOString());
  const events = run.events ?? [];
  return {
    run_id: run.id,
    request: {
      engine: 'direct',
      brief: project.brief ?? '',
      reference_context: '',
      source_url: project.source_url,
      locale: 'ru',
      creativity: 0.9,
      viewport_targets: ['desktop', 'mobile'],
      max_repairs: 3,
      contract_id: 'chat-v1',
      creative_profile: 'balanced',
      visual_repair_limit: 8,
    },
    status,
    progress: run.progress,
    created_at: run.created_at,
    updated_at: run.finished_at ?? run.started_at ?? run.created_at,
    latest_sequence: run.latest_sequence,
    artifact: preview && run.preview?.source !== 'restorable_draft' ? preview : null,
    draft_artifact: preview && run.preview?.source === 'restorable_draft' ? preview : null,
    quality_status: run.preview?.quality_status ?? (preview ? 'accepted' : 'pending'),
    usage: aggregateSaasUsage(events),
    elapsed_seconds: Number.isNaN(createdAt) || Number.isNaN(finishedAt)
      ? 0
      : Math.max(0, (finishedAt - createdAt) / 1_000),
    error_code: run.error_code,
    cancel_requested: false,
  };
}

function saasError(error: unknown, fallback: string): StudioError {
  if (error instanceof BuilderApiError) {
    const message = error.code === 'trial_consumed'
      ? 'Бесплатная генерация уже использована. Сохранённый результат остаётся доступен.'
      : error.code === 'trial_retry_unavailable'
        ? 'Повтор пока недоступен: проверяем итог предыдущего запуска.'
        : error.code === 'run_is_not_active'
          ? 'У проекта уже есть другой активный запуск. Обновите страницу.'
          : error.code === 'run_not_retryable'
            ? 'Повтор доступен только после ошибки или отмены.'
            : error.status === 401
      ? 'Войдите в аккаунт, чтобы открыть проект.'
      : error.status === 403
        ? 'Нет доступа к этому действию. Обновите страницу и войдите снова.'
        : error.status === 404
          ? 'Проект не найден или у вас нет к нему доступа.'
          : error.status === 409
            ? 'Бесплатный запуск уже использован. Сохранённый результат остаётся доступен.'
            : error.status >= 500
              ? 'Сервис временно недоступен. Сохранённые данные не потеряны.'
              : fallback;
    return { message, raw: error.raw, code: error.code };
  }
  return {
    message: fallback,
    raw: error instanceof Error ? error.message : String(error),
    code: null,
  };
}

function isNonRetryableClientError(error: unknown): error is BuilderApiError {
  return error instanceof BuilderApiError
    && error.status >= 400
    && error.status < 500
    && !error.retryable;
}

function idempotencyKey(projectId: string) {
  const storageKey = `kaigo.saas.project.${projectId}.idempotency-key`;
  // A project-scoped value closes the localStorage first-write race between tabs.
  // Project UUIDs are unique, and the free project flow intentionally has one first run.
  const projectKey = `studio-${projectId}`;
  try {
    const saved = localStorage.getItem(storageKey);
    if (saved) return saved;
    localStorage.setItem(storageKey, projectKey);
  } catch {
    // Determinism still prevents duplicate server runs when storage is unavailable.
  }
  return projectKey;
}

function retryIdempotencyKey(runId: string) {
  const storageKey = `kaigo.saas.run.${runId}.retry-idempotency-key`;
  const runKey = `studio-retry-${runId}`;
  try {
    const saved = localStorage.getItem(storageKey);
    if (saved) return saved;
    localStorage.setItem(storageKey, runKey);
  } catch {
    // The deterministic server key remains safe when browser storage is unavailable.
  }
  return runKey;
}

function activityFor(status: BuilderRunStatus) {
  if (status === 'queued' || status === 'created') return 'Запуск в очереди';
  if (status === 'running') return 'Генерация выполняется';
  if (status === 'completed') return 'Готово — виджет сохранён';
  if (status === 'failed') return 'Генерация завершилась с ошибкой';
  return 'Генерация отменена';
}

function useSaasProjectRun(projectId: string | null): BuilderRunController {
  const [project, setProject] = useState<SaasProject | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<BuilderRunSnapshot | null>(null);
  const [events, setEvents] = useState<BuilderEvent[]>([]);
  const [error, setError] = useState<StudioError | null>(null);
  const [activityMessage, setActivityMessage] = useState('Загружаем проект');
  const [connection, setConnection] = useState<'idle' | 'streaming' | 'polling'>('idle');
  const [isHydrating, setIsHydrating] = useState(Boolean(projectId));
  const [mutationPending, setMutationPending] = useState(false);
  const [csrfToken, setCsrfToken] = useState<string | null>(null);
  const csrfRef = useRef<string | null>(null);
  const projectRef = useRef<SaasProject | null>(null);
  const runRef = useRef<SaasRunSnapshot | null>(null);
  const lastSequenceRef = useRef(0);
  const mutationPendingRef = useRef(false);

  const applyRun = useCallback((owner: SaasProject, run: SaasRunSnapshot) => {
    if (run.project_id !== owner.id) return;
    const previous = runRef.current;
    if (previous?.id === run.id && run.latest_sequence < Math.max(previous.latest_sequence, lastSequenceRef.current)) return;
    if (previous?.id !== run.id) lastSequenceRef.current = 0;
    runRef.current = run;
    const hydratedSequence = Math.max(0, ...(run.events ?? []).map((event) => event.sequence));
    lastSequenceRef.current = Math.max(lastSequenceRef.current, hydratedSequence);
    setRunId(run.id);
    setSnapshot(adaptSaasRun(owner, run));
    if (run.events) {
      setEvents((current) => {
        const unique = new Map<number, BuilderEvent>();
        if (previous?.id === run.id) {
          for (const item of current) unique.set(item.sequence, item);
        }
        for (const item of run.events ?? []) unique.set(item.sequence, adaptSaasEvent(run.id, item));
        return [...unique.values()].sort((left, right) => left.sequence - right.sequence);
      });
    }
    const status = saasStatus(run);
    setActivityMessage(activityFor(status));
    if (status === 'failed') {
      setError({
        message: run.preview
          ? 'Генерация остановилась, но рабочий черновик сохранён.'
          : 'Генерация завершилась с ошибкой.',
        raw: run.error_message ?? run.error_code ?? 'Подробности не переданы',
        code: run.error_code,
      });
    } else if (status !== 'cancelled') {
      setError(null);
    }
  }, []);

  useEffect(() => {
    if (!projectId) return;
    const abort = new AbortController();
    csrfRef.current = null;
    setCsrfToken(null);
    setIsHydrating(true);
    const hydrate = async () => {
      try {
        const session = await getAuthSession();
        if (!session.authenticated || !session.csrf_token) {
          throw new BuilderApiError('authentication_required', {
            status: 401,
            code: 'authentication_required',
            raw: 'authentication_required',
          });
        }
        csrfRef.current = session.csrf_token;
        setCsrfToken(session.csrf_token);
        const nextProject = await getProject(projectId);
        if (abort.signal.aborted) return;
        projectRef.current = nextProject;
        setProject(nextProject);
        if (nextProject.active_run) {
          const run = await getProjectRun(nextProject.active_run.id, abort.signal);
          if (!abort.signal.aborted) applyRun(nextProject, run);
        } else {
          setActivityMessage('Проект готов к запуску');
        }
      } catch (caught) {
        if (!abort.signal.aborted) {
          setError(saasError(caught, 'Не удалось загрузить проект.'));
          setActivityMessage('Проект не загружен');
        }
      } finally {
        if (!abort.signal.aborted) setIsHydrating(false);
      }
    };
    void hydrate();
    return () => abort.abort();
  }, [applyRun, projectId]);

  useEffect(() => {
    if (!projectId || !runId || TERMINAL_STATUSES.has(snapshot?.status ?? 'created')) {
      setConnection('idle');
      return;
    }
    const abort = new AbortController();
    let pollTimer: ReturnType<typeof setTimeout> | null = null;
    let refreshPromise: Promise<SaasRunSnapshot> | null = null;
    let pollingStopped = false;
    let retryCount = 0;

    const refresh = async () => {
      if (refreshPromise) return refreshPromise;
      const request = getProjectRun(runId, abort.signal).then((run) => {
        const owner = projectRef.current;
        if (!abort.signal.aborted && owner) applyRun(owner, run);
        return run;
      });
      refreshPromise = request;
      try {
        return await request;
      } finally {
        if (refreshPromise === request) refreshPromise = null;
      }
    };
    const stopPolling = () => {
      pollingStopped = true;
      if (pollTimer) clearTimeout(pollTimer);
      pollTimer = null;
      if (!abort.signal.aborted) setConnection('idle');
    };
    const schedulePolling = (delay: number) => {
      if (abort.signal.aborted || pollingStopped || pollTimer) return;
      pollTimer = setTimeout(async () => {
        pollTimer = null;
        if (abort.signal.aborted || pollingStopped) return;
        try {
          const run = await refresh();
          retryCount = 0;
          if (TERMINAL_STATUSES.has(saasStatus(run))) {
            stopPolling();
            return;
          }
          schedulePolling(POLL_INTERVAL_MS);
        } catch (caught) {
          if (abort.signal.aborted) return;
          setError(saasError(caught, 'Не удалось обновить состояние запуска.'));
          if (isNonRetryableClientError(caught)) {
            stopPolling();
            return;
          }
          retryCount += 1;
          schedulePolling(Math.min(POLL_MAX_INTERVAL_MS, POLL_INTERVAL_MS * (2 ** retryCount)));
        }
      }, delay);
    };
    const startPolling = () => {
      if (abort.signal.aborted || pollingStopped || pollTimer) return;
      setConnection('polling');
      schedulePolling(POLL_INTERVAL_MS);
    };
    const onEvent = (incoming: SaasEvent) => {
      if (abort.signal.aborted || incoming.sequence <= lastSequenceRef.current) return;
      lastSequenceRef.current = incoming.sequence;
      const adapted = adaptSaasEvent(runId, incoming);
      setEvents((current) => [...current, adapted].sort((left, right) => left.sequence - right.sequence));
      setActivityMessage(adapted.message || 'Генерация выполняется');
      void refresh().catch((caught) => {
        if (abort.signal.aborted) return;
        setError(saasError(caught, 'Не удалось обновить состояние запуска.'));
        if (isNonRetryableClientError(caught)) stopPolling();
      });
    };

    setConnection('streaming');
    void streamProjectRunEvents(runId, lastSequenceRef.current, onEvent, abort.signal)
      .then(() => {
        if (!abort.signal.aborted && !TERMINAL_STATUSES.has(runRef.current ? saasStatus(runRef.current) : 'created')) {
          startPolling();
        }
      })
      .catch((caught) => {
        if (!abort.signal.aborted) {
          if (isNonRetryableClientError(caught)) {
            setError(saasError(caught, 'Не удалось подключиться к событиям запуска.'));
            stopPolling();
            return;
          }
          startPolling();
        }
      });

    return () => {
      abort.abort();
      if (pollTimer) clearTimeout(pollTimer);
    };
  }, [applyRun, projectId, runId, snapshot?.status]);

  const createRun: BuilderRunController['createRun'] = useCallback(async (input) => {
    const owner = projectRef.current;
    const csrf = csrfRef.current;
    if (!projectId || !owner || !csrf || mutationPendingRef.current) return;
    mutationPendingRef.current = true;
    setMutationPending(true);
    setError(null);
    setActivityMessage('Создаём запуск');
    try {
      const saved = await updateProjectDraft(
        projectId,
        input.source_url,
        input.brief,
        csrf,
      );
      projectRef.current = saved;
      setProject(saved);
      const run = await createProjectRun(projectId, csrf, idempotencyKey(projectId));
      const updated = { ...saved, status: run.status, active_run: run };
      projectRef.current = updated;
      setProject(updated);
      applyRun(updated, run);
    } catch (caught) {
      setError(saasError(caught, 'Не удалось создать запуск.'));
      setActivityMessage('Запуск не создан');
    } finally {
      mutationPendingRef.current = false;
      setMutationPending(false);
    }
  }, [applyRun, projectId]);

  const cancelRun: BuilderRunController['cancelRun'] = useCallback(async () => {
    const target = runRef.current;
    const csrf = csrfRef.current;
    if (
      !target
      || !csrf
      || !['queued', 'running'].includes(saasStatus(target))
      || mutationPendingRef.current
    ) return;
    mutationPendingRef.current = true;
    setMutationPending(true);
    setError(null);
    setActivityMessage('Запрашиваем безопасную отмену');
    try {
      await cancelProjectRun(target.id, csrf);
      setActivityMessage('Отмена запрошена — генерация остановится безопасно');
    } catch (caught) {
      setError(saasError(caught, 'Не удалось отменить генерацию.'));
      setActivityMessage('Отмена не выполнена');
    } finally {
      mutationPendingRef.current = false;
      setMutationPending(false);
    }
  }, []);

  const retryRun: BuilderRunController['retryRun'] = useCallback(async () => {
    const owner = projectRef.current;
    const source = runRef.current;
    const csrf = csrfRef.current;
    if (
      !owner
      || !source
      || !csrf
      || !['failed', 'cancelled'].includes(saasStatus(source))
      || mutationPendingRef.current
    ) return;
    mutationPendingRef.current = true;
    setMutationPending(true);
    setError(null);
    setActivityMessage('Проверяем возможность безопасного повтора');
    try {
      const replacement = await retryProjectRun(
        source.id,
        csrf,
        retryIdempotencyKey(source.id),
      );
      const updated = {
        ...owner,
        status: replacement.status,
        active_run: replacement,
      };
      projectRef.current = updated;
      setProject(updated);
      applyRun(updated, replacement);
    } catch (caught) {
      setError(saasError(caught, 'Не удалось повторить генерацию.'));
      setActivityMessage('Повтор не запущен');
    } finally {
      mutationPendingRef.current = false;
      setMutationPending(false);
    }
  }, [applyRun]);

  const unavailable = useCallback(async () => {
    setError({
      message: 'Дальнейшая доработка будет доступна на тарифе.',
      raw: 'billing_not_implemented',
      code: 'upgrade_required',
    });
  }, []);

  return {
    project,
    projectMode: Boolean(projectId),
    csrfToken,
    runId,
    snapshot,
    events,
    error,
    activityMessage,
    connection,
    isHydrating,
    mutationPending,
    createRun,
    cancelRun,
    retryRun,
    refineRun: unavailable,
    clearError: () => setError(null),
  };
}

export function useBuilderRun(
  projectId: string | null = null,
  allowLegacyBuilder = true,
): BuilderRunController {
  const legacy = useLegacyBuilderRun(allowLegacyBuilder && !projectId);
  const saas = useSaasProjectRun(projectId);
  return projectId || !allowLegacyBuilder ? saas : legacy;
}
