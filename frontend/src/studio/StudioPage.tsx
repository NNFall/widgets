import {
  ArrowLeft,
  ArrowRight,
  ArrowsClockwise,
  CheckCircle,
  Clock,
  CloudArrowUp,
  Code,
  Eye,
  Link as LinkIcon,
  PaperPlaneTilt,
  StopCircle,
} from '@phosphor-icons/react';
import { motion } from 'motion/react';
import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';

import { KaigoLogo } from '../shared/KaigoLogo';
import { campaignFromSearch } from '../shared/campaign';
import { canonicalWebsiteUrl } from '../shared/UrlComposer';
import { StudioPreview } from './StudioPreview';
import { StudioTimeline } from './StudioTimeline';
import { StudioComposer } from './StudioComposer';
import { UpgradeGate } from './UpgradeGate';
import type { BuilderEngine, BuilderRunSnapshot, PreviewViewport, StudioError } from './types';
import { useBuilderRun } from './useBuilderRun';
import { createProject, getAuthSession } from './api';

const DEFAULT_BRIEF = 'Создай компактного AI-сотрудника, который консультирует посетителей по подтверждённым данным этого сайта.';
const numberFormatter = new Intl.NumberFormat('ru-RU');
const decimalFormatter = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 });

function querySourceUrl() {
  return new URLSearchParams(window.location.search).get('url') ?? '';
}

function queryProjectId() {
  return new URLSearchParams(window.location.search).get('project');
}

function validateSourceUrl(value: string) {
  if (!value.trim()) return 'Вставьте HTTPS-ссылку на сайт.';
  if (!canonicalWebsiteUrl(value)) return 'Нужна публичная HTTPS-ссылка без параметров и авторизации.';
  return null;
}

function selectedArtifact(snapshot: BuilderRunSnapshot | null) {
  return snapshot?.draft_artifact ?? snapshot?.artifact ?? null;
}

function progressFor(snapshot: BuilderRunSnapshot | null, eventCount: number) {
  if (!snapshot) return 0;
  if (snapshot.status === 'completed') return 100;
  if (typeof snapshot.progress === 'number') return Math.max(0, Math.min(100, Math.round(snapshot.progress)));
  if (snapshot.status === 'failed' || snapshot.status === 'cancelled') return Math.min(96, 16 + eventCount * 7);
  return Math.min(92, 12 + eventCount * 7 + (selectedArtifact(snapshot) ? 18 : 0));
}

function readyQuality(status: string | undefined) {
  return status === 'verified' || status === 'accepted';
}

function readyFreeResult(snapshot: BuilderRunSnapshot | null) {
  const artifact = snapshot?.artifact;
  return snapshot?.status === 'completed'
    && readyQuality(snapshot.quality_status)
    && artifact?.source === 'accepted_artifact'
    && Boolean(artifact.id);
}

type ErrorPersistenceState = 'saved_artifact' | 'terminal_without_artifact' | 'unknown';

function errorPersistenceState(snapshot: BuilderRunSnapshot | null): ErrorPersistenceState {
  if (selectedArtifact(snapshot)) return 'saved_artifact';
  if (
    snapshot
    && (snapshot.status === 'failed' || snapshot.status === 'cancelled')
    && snapshot.artifact === null
    && snapshot.draft_artifact === null
  ) {
    return 'terminal_without_artifact';
  }
  return 'unknown';
}

function ErrorNotice({
  error,
  persistence,
}: {
  error: StudioError;
  persistence: ErrorPersistenceState;
}) {
  return (
    <div className="studio-error" role="alert">
      <div className="studio-error__mark" aria-hidden>!</div>
      <div>
        <strong>{error.message}</strong>
        {persistence === 'saved_artifact' && (
          <p>Последняя доступная версия и история запуска сохранены.</p>
        )}
        {persistence === 'terminal_without_artifact' && (
          <p>История запуска сохранена, но версия виджета не была создана.</p>
        )}
        <details>
          <summary>Детали</summary>
          <pre>{error.raw}</pre>
        </details>
      </div>
    </div>
  );
}

export function StudioPage() {
  const [projectId, setProjectId] = useState(queryProjectId);
  const legacyBuilder = (window.location.pathname.replace(/\/+$/, '') || '/') === '/builder';
  const controller = useBuilderRun(projectId, legacyBuilder);
  const [sourceUrl, setSourceUrl] = useState(querySourceUrl);
  const [brief, setBrief] = useState('');
  const [engine, setEngine] = useState<BuilderEngine>('direct');
  const [creativity, setCreativity] = useState(0.9);
  const [refinement, setRefinement] = useState('');
  const [viewport, setViewport] = useState<PreviewViewport>('desktop');
  const [formError, setFormError] = useState<string | null>(null);
  const [projectPending, setProjectPending] = useState(false);
  const hydratedRun = useRef<string | null>(null);
  const previewAnchorRef = useRef<HTMLElement>(null);
  const artifact = selectedArtifact(controller.snapshot);
  const persistence = errorPersistenceState(controller.snapshot);
  const freeResultReady = readyFreeResult(controller.snapshot);
  const status = controller.snapshot?.status ?? null;
  const running = status === 'created' || status === 'queued' || status === 'running';
  const controlsLocked = running || controller.mutationPending;
  const progress = progressFor(controller.snapshot, controller.events.length);
  const refinable = !controller.projectMode && status === 'completed'
    && controller.snapshot?.request.engine === 'direct'
    && Boolean(artifact);

  useEffect(() => {
    const next = controller.snapshot;
    if (!next || hydratedRun.current === next.run_id) return;
    hydratedRun.current = next.run_id;
    setSourceUrl(next.request.source_url);
    setBrief(next.request.brief === DEFAULT_BRIEF ? '' : next.request.brief);
    setEngine(next.request.engine);
    setCreativity(next.request.creativity);
  }, [controller.snapshot]);

  useEffect(() => {
    if (!controller.project || controller.snapshot) return;
    setSourceUrl(controller.project.source_url);
    setBrief(controller.project.brief ?? '');
  }, [controller.project, controller.snapshot]);

  const formattedSession = useMemo(() => {
    if (!controller.runId) return 'Новая сессия';
    const suffix = controller.runId.length > 10 ? controller.runId.slice(-8) : controller.runId;
    return `Сессия ${suffix}`;
  }, [controller.runId]);

  const submitRun = async (submitEvent: FormEvent) => {
    submitEvent.preventDefault();
    const validationMessage = validateSourceUrl(sourceUrl);
    if (validationMessage) {
      setFormError(validationMessage);
      return;
    }
    setFormError(null);
    controller.clearError();
    const canonicalUrl = canonicalWebsiteUrl(sourceUrl);
    if (!canonicalUrl) return;
    await controller.createRun({
      source_url: canonicalUrl,
      brief: brief.trim() || DEFAULT_BRIEF,
      engine,
      creativity,
      locale: 'ru',
      max_repairs: 3,
    });
  };

  const submitProject = async (submitEvent: FormEvent) => {
    submitEvent.preventDefault();
    const validationMessage = validateSourceUrl(sourceUrl);
    if (validationMessage) {
      setFormError(validationMessage);
      return;
    }
    setFormError(null);
    setProjectPending(true);
    try {
      const canonicalUrl = canonicalWebsiteUrl(sourceUrl);
      if (!canonicalUrl) return;
      const session = await getAuthSession();
      if (!session.authenticated || !session.csrf_token) {
        setFormError('Войдите снова, чтобы создать проект.');
        return;
      }
      const project = await createProject(
        canonicalUrl,
        brief.trim(),
        session.csrf_token,
        campaignFromSearch(),
      );
      const params = new URLSearchParams(window.location.search);
      params.delete('url');
      params.set('project', project.id);
      window.history.replaceState({}, '', `/studio?${params.toString()}`);
      setProjectId(project.id);
    } catch {
      setFormError('Не удалось создать проект. Попробуйте ещё раз.');
    } finally {
      setProjectPending(false);
    }
  };

  const submitRefinement = async (submitEvent: FormEvent) => {
    submitEvent.preventDefault();
    const message = refinement.trim();
    if (!message) return;
    setRefinement('');
    await controller.refineRun(message);
  };

  const scrollToPreview = () => {
    previewAnchorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  if (!projectId && !legacyBuilder) {
    return (
      <div className="studio-app studio-app--composer">
        <header className="studio-header">
          <a className="studio-header__logo" href="/" aria-label="Kaigo — на главную">
            <KaigoLogo />
          </a>
          <div className="studio-header__session">
            <span>Kaigo Studio</span>
            <strong>Новый проект</strong>
          </div>
        </header>
        <main className="studio-shell studio-shell--composer">
          <section className="studio-composer" aria-labelledby="new-project-title">
            <div className="studio-composer__card">
              <p className="studio-kicker">Бесплатная экспресс-версия</p>
              <h1 id="new-project-title">Новый проект в Kaigo Studio</h1>
              <p>Добавьте сайт и пожелание. Проект сохранится в вашем аккаунте до запуска.</p>
              <form onSubmit={submitProject}>
                <label htmlFor="new-project-url">Ссылка на сайт</label>
                <input
                  id="new-project-url"
                  type="url"
                  value={sourceUrl}
                  onChange={(event) => setSourceUrl(event.target.value)}
                />
                <label htmlFor="new-project-brief">Пожелание к AI-виджету</label>
                <textarea
                  id="new-project-brief"
                  maxLength={4_000}
                  value={brief}
                  onChange={(event) => setBrief(event.target.value)}
                />
                {formError && <p className="studio-form__error" role="alert">{formError}</p>}
                <button type="submit" className="studio-create" disabled={projectPending}>
                  {projectPending ? <Clock aria-hidden size={20} /> : <PaperPlaneTilt aria-hidden size={20} weight="fill" />}
                  Создать проект
                </button>
              </form>
            </div>
          </section>
        </main>
      </div>
    );
  }

  if (controller.projectMode && !controller.runId) {
    return (
      <div className="studio-app studio-app--composer">
        <header className="studio-header">
          <a className="studio-header__logo" href="/" aria-label="Kaigo — на главную">
            <KaigoLogo />
          </a>
          <div className="studio-header__session" aria-live="polite">
            <span>Kaigo Studio</span>
            <strong data-connection={controller.connection}>{controller.activityMessage}</strong>
            {controller.connection === 'polling' && <small>Резервный режим обновления</small>}
          </div>
        </header>
        <main className="studio-shell studio-shell--composer">
          {controller.error && !controller.project ? (
            <ErrorNotice error={controller.error} persistence={persistence} />
          ) : controller.isHydrating || !controller.project ? (
            <p className="studio-composer__loading" role="status">Загружаем проект…</p>
          ) : (
            <StudioComposer
              sourceUrl={sourceUrl}
              brief={brief}
              pending={controller.mutationPending}
              error={formError}
              onSourceUrlChange={setSourceUrl}
              onBriefChange={setBrief}
              onSubmit={submitRun}
            />
          )}
          {controller.error && controller.project && (
            <ErrorNotice error={controller.error} persistence={persistence} />
          )}
        </main>
      </div>
    );
  }

  return (
    <div className="studio-app">
      <header className="studio-header">
        <a className="studio-header__logo" href="/" aria-label="Kaigo — на главную">
          <KaigoLogo />
        </a>
        <div className="studio-header__session">
          <span>{formattedSession}</span>
          <strong data-connection={controller.connection}>{controller.activityMessage}</strong>
          {controller.connection === 'polling' && <small>Резервный режим обновления</small>}
        </div>
        <div className="studio-header__actions">
          <button type="button" className="studio-header__preview" onClick={scrollToPreview}>
            <Eye aria-hidden size={19} /> Предпросмотр
          </button>
          <button
            type="button"
            className="studio-header__publish"
            disabled={!freeResultReady}
            aria-label={freeResultReady ? 'Перейти к публикации' : 'Публикация станет доступна после проверенного результата'}
            style={freeResultReady ? { color: 'var(--ink)' } : undefined}
            onClick={() => document.getElementById('studio-publication')?.scrollIntoView({
              behavior: 'smooth',
              block: 'center',
            })}
          >
            <CloudArrowUp aria-hidden size={19} /> Опубликовать
            {!freeResultReady && <span>После проверки</span>}
          </button>
        </div>
      </header>

      <main className="studio-shell">
        <motion.aside
          className="studio-rail"
          initial={false}
          animate={{ opacity: 1, x: 0 }}
          transition={{ type: 'spring', stiffness: 105, damping: 22 }}
        >
          <a className="studio-back" href="/">
            <ArrowLeft aria-hidden size={17} /> На главную
          </a>
          <div className="studio-intro">
            <p className="studio-kicker">Kaigo Studio</p>
            <h1>Студия Kaigo</h1>
            <p>
              {controller.projectMode
                ? 'Проверяйте готовую версию, тестируйте ответы в предпросмотре и публикуйте принятый виджет.'
                : 'Задайте направление, следите за проверками и дорабатывайте каждую принятую версию.'}
            </p>
          </div>

          <form className="studio-form" onSubmit={submitRun}>
            <label htmlFor="studio-source-url">Ссылка на сайт</label>
            <div className="studio-input-wrap">
              <LinkIcon aria-hidden size={19} />
              <input
                id="studio-source-url"
                type="url"
                inputMode="url"
                autoComplete="url"
                placeholder="https://example.com"
                value={sourceUrl}
                onChange={(event) => setSourceUrl(event.target.value)}
                readOnly={controller.projectMode}
                disabled={controlsLocked}
              />
            </div>
            <p className="studio-helper">Публичная HTTPS-страница без параметров в адресе.</p>

            <label htmlFor="studio-brief">Пожелание к AI-сотруднику</label>
            <textarea
              id="studio-brief"
              placeholder="Например: уверенный консультант, который говорит простым языком"
              maxLength={4_000}
              value={brief}
              onChange={(event) => setBrief(event.target.value)}
              readOnly={controller.projectMode}
              disabled={controlsLocked}
            />

            <details className="studio-advanced">
              <summary>Параметры прототипа</summary>
              <div>
                <label htmlFor="studio-engine">Движок</label>
                <select id="studio-engine" value={engine} onChange={(event) => setEngine(event.target.value as BuilderEngine)} disabled={controller.projectMode || controlsLocked}>
                  <option value="direct">Gemini staged</option>
                  <option value="antigravity">Antigravity agent</option>
                </select>
                <label htmlFor="studio-creativity">Творчество</label>
                <input
                  id="studio-creativity"
                  type="number"
                  min="0"
                  max="2"
                  step="0.05"
                  value={creativity}
                  onChange={(event) => setCreativity(Number(event.target.value))}
                  disabled={controller.projectMode || controlsLocked || engine !== 'direct'}
                />
              </div>
            </details>

            {formError && <p className="studio-form__error" role="alert">{formError}</p>}
            <button type="submit" className="studio-create" disabled={controller.projectMode || running || controller.isHydrating || controller.mutationPending}>
              {controller.isHydrating || controller.mutationPending ? <Clock aria-hidden size={20} /> : <PaperPlaneTilt aria-hidden size={20} weight="fill" />}
              {running ? 'Генерация идёт' : 'Создать AI-виджет'}
              {!running && !controller.isHydrating && !controller.mutationPending && <ArrowRight aria-hidden size={18} />}
            </button>
          </form>

          {controller.runId && (
            <div className="studio-progress" aria-label="Прогресс генерации">
              <div><span>Прогресс</span><strong>{progress}%</strong></div>
              <div className="studio-progress__track"><span style={{ transform: `scaleX(${progress / 100})` }} /></div>
              <p>{controller.activityMessage}</p>
            </div>
          )}

          <div className="studio-run-actions">
            <button type="button" onClick={() => void controller.cancelRun()} disabled={!running || controller.mutationPending}>
              <StopCircle aria-hidden size={18} /> Отменить генерацию
            </button>
            <button type="button" onClick={() => void controller.retryRun()} disabled={controller.mutationPending || (status !== 'failed' && status !== 'cancelled')}>
              <ArrowsClockwise aria-hidden size={18} /> Повторить запуск
            </button>
          </div>

          {(controller.error || formError) && controller.error && (
            <ErrorNotice error={controller.error} persistence={persistence} />
          )}

          <StudioTimeline events={controller.events} running={running} />

          {controller.projectMode ? (
            <div className="studio-refine">
              <strong>Проверка диалога</strong>
              <p>Чат в предпросмотре проверяет ответы виджета, но не изменяет его.</p>
            </div>
          ) : (
            <form className="studio-refine" onSubmit={submitRefinement}>
              <label htmlFor="studio-refinement">Что изменить в виджете?</label>
              <div>
                <textarea
                  id="studio-refinement"
                  maxLength={2_000}
                  placeholder="Например: сделай приветствие короче"
                  value={refinement}
                  onChange={(event) => setRefinement(event.target.value)}
                  onKeyDown={(event) => {
                    if (
                      event.key === 'Enter'
                      && (event.ctrlKey || event.metaKey)
                      && refinable
                      && !controller.mutationPending
                      && refinement.trim()
                    ) {
                      event.preventDefault();
                      event.currentTarget.form?.requestSubmit();
                    }
                  }}
                  disabled={!refinable || controller.mutationPending}
                />
                <button type="submit" disabled={!refinable || controller.mutationPending || !refinement.trim()} aria-label="Применить изменение">
                  <PaperPlaneTilt aria-hidden size={19} weight="fill" />
                </button>
              </div>
              <p>{refinable ? 'Ctrl + Enter тоже отправляет пожелание' : 'Доработка откроется после проверенной версии'}</p>
            </form>
          )}
        </motion.aside>

        <motion.section
          className="studio-workspace"
          ref={previewAnchorRef}
          initial={false}
          animate={{ opacity: 1, y: 0 }}
          transition={{ type: 'spring', stiffness: 95, damping: 22, delay: 0.08 }}
        >
          <div className="studio-workspace__metrics" aria-label="Метрики запуска">
            <div><span>Ревизия</span><strong>{artifact?.revision ?? '—'}</strong></div>
            <div><span>Токены</span><strong>{controller.snapshot ? numberFormatter.format(controller.snapshot.usage.total_tokens) : '—'}</strong></div>
            <div><span>Время</span><strong>{controller.snapshot ? `${decimalFormatter.format(controller.snapshot.elapsed_seconds)} с` : '—'}</strong></div>
            <div className="studio-workspace__quality">
              <CheckCircle aria-hidden size={19} weight={readyQuality(controller.snapshot?.quality_status) ? 'fill' : 'regular'} />
              <span>{controller.snapshot?.quality_status === 'accepted' ? 'Готово' : controller.snapshot?.quality_status === 'verified' ? 'Проверено' : 'Черновик'}</span>
            </div>
          </div>
          <StudioPreview
            runId={controller.runId}
            revision={artifact?.revision ?? null}
            projectMode={controller.projectMode}
            csrfToken={controller.csrfToken}
            artDirection={artifact?.art_direction ?? ''}
            qualityStatus={controller.snapshot?.quality_status ?? 'pending'}
            viewport={viewport}
            onViewportChange={setViewport}
          />
          {controller.projectMode && projectId && artifact?.id && freeResultReady && (
            <UpgradeGate
              csrfToken={controller.csrfToken}
              projectId={projectId}
              artifactId={artifact.id}
              revision={artifact.revision}
            />
          )}
          <div className="studio-workspace__footer">
            <Code aria-hidden size={18} />
            <span>Preview использует изолированный runtime сборщика: launcher, composer и chat bridge работают внутри sandbox.</span>
          </div>
        </motion.section>
      </main>
    </div>
  );
}
