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
import { StudioPreview } from './StudioPreview';
import { StudioTimeline } from './StudioTimeline';
import type { BuilderEngine, BuilderRunSnapshot, PreviewViewport, StudioError } from './types';
import { useBuilderRun } from './useBuilderRun';

const DEFAULT_BRIEF = 'Создай компактного AI-сотрудника, который консультирует посетителей по подтверждённым данным этого сайта.';
const numberFormatter = new Intl.NumberFormat('ru-RU');
const decimalFormatter = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 });

function querySourceUrl() {
  return new URLSearchParams(window.location.search).get('url') ?? '';
}

function validateSourceUrl(value: string) {
  if (!value.trim()) return 'Вставьте HTTPS-ссылку на сайт.';
  try {
    const url = new URL(value);
    if (
      url.protocol !== 'https:'
      || url.username
      || url.password
      || url.port
      || url.search
      || url.hash
    ) return 'Нужна публичная HTTPS-ссылка без параметров и авторизации.';
  } catch {
    return 'Ссылка на сайт некорректна.';
  }
  return null;
}

function selectedArtifact(snapshot: BuilderRunSnapshot | null) {
  return snapshot?.draft_artifact ?? snapshot?.artifact ?? null;
}

function progressFor(snapshot: BuilderRunSnapshot | null, eventCount: number) {
  if (!snapshot) return 0;
  if (snapshot.status === 'completed') return 100;
  if (snapshot.status === 'failed' || snapshot.status === 'cancelled') return Math.min(96, 16 + eventCount * 7);
  return Math.min(92, 12 + eventCount * 7 + (selectedArtifact(snapshot) ? 18 : 0));
}

function ErrorNotice({ error }: { error: StudioError }) {
  return (
    <div className="studio-error" role="alert">
      <div className="studio-error__mark" aria-hidden>!</div>
      <div>
        <strong>{error.message}</strong>
        <p>Последняя доступная версия и история запуска сохранены.</p>
        <details>
          <summary>Детали</summary>
          <pre>{error.raw}</pre>
        </details>
      </div>
    </div>
  );
}

export function StudioPage() {
  const controller = useBuilderRun();
  const [sourceUrl, setSourceUrl] = useState(querySourceUrl);
  const [brief, setBrief] = useState('');
  const [engine, setEngine] = useState<BuilderEngine>('direct');
  const [creativity, setCreativity] = useState(0.9);
  const [refinement, setRefinement] = useState('');
  const [viewport, setViewport] = useState<PreviewViewport>('desktop');
  const [formError, setFormError] = useState<string | null>(null);
  const hydratedRun = useRef<string | null>(null);
  const previewAnchorRef = useRef<HTMLElement>(null);
  const artifact = selectedArtifact(controller.snapshot);
  const status = controller.snapshot?.status ?? null;
  const running = status === 'created' || status === 'running';
  const progress = progressFor(controller.snapshot, controller.events.length);
  const refinable = status === 'completed'
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
    await controller.createRun({
      source_url: sourceUrl.trim(),
      brief: brief.trim() || DEFAULT_BRIEF,
      engine,
      creativity,
      locale: 'ru',
      max_repairs: 3,
    });
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

  return (
    <div className="studio-app">
      <header className="studio-header">
        <a className="studio-header__logo" href="/" aria-label="Kaigo — на главную">
          <KaigoLogo />
        </a>
        <div className="studio-header__session">
          <span>{formattedSession}</span>
          <strong data-connection={controller.connection}>{controller.activityMessage}</strong>
        </div>
        <div className="studio-header__actions">
          <button type="button" className="studio-header__preview" onClick={scrollToPreview}>
            <Eye aria-hidden size={19} /> Предпросмотр
          </button>
          <button type="button" className="studio-header__publish" disabled aria-label="Опубликовать — Скоро">
            <CloudArrowUp aria-hidden size={19} /> Опубликовать <span>Скоро</span>
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
            <p>Задайте направление, следите за проверками и дорабатывайте каждую принятую версию.</p>
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
                disabled={running}
              />
            </div>
            <p className="studio-helper">Публичная HTTPS-страница без параметров в адресе.</p>

            <label htmlFor="studio-brief">Пожелание к AI-сотруднику</label>
            <textarea
              id="studio-brief"
              placeholder="Например: уверенный консультант, который говорит простым языком"
              maxLength={12_000}
              value={brief}
              onChange={(event) => setBrief(event.target.value)}
              disabled={running}
            />

            <details className="studio-advanced">
              <summary>Параметры прототипа</summary>
              <div>
                <label htmlFor="studio-engine">Движок</label>
                <select id="studio-engine" value={engine} onChange={(event) => setEngine(event.target.value as BuilderEngine)} disabled={running}>
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
                  disabled={running || engine !== 'direct'}
                />
              </div>
            </details>

            {formError && <p className="studio-form__error" role="alert">{formError}</p>}
            <button type="submit" className="studio-create" disabled={running || controller.isHydrating}>
              {controller.isHydrating ? <Clock aria-hidden size={20} /> : <PaperPlaneTilt aria-hidden size={20} weight="fill" />}
              {running ? 'Генерация идёт' : 'Создать AI-виджет'}
              {!running && !controller.isHydrating && <ArrowRight aria-hidden size={18} />}
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
            <button type="button" onClick={() => void controller.cancelRun()} disabled={!running}>
              <StopCircle aria-hidden size={18} /> Отменить генерацию
            </button>
            <button type="button" onClick={() => void controller.retryRun()} disabled={status !== 'failed' && status !== 'cancelled'}>
              <ArrowsClockwise aria-hidden size={18} /> Повторить запуск
            </button>
          </div>

          {(controller.error || formError) && controller.error && <ErrorNotice error={controller.error} />}

          <StudioTimeline events={controller.events} running={running} />

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
                  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && refinable && refinement.trim()) {
                    event.preventDefault();
                    event.currentTarget.form?.requestSubmit();
                  }
                }}
                disabled={!refinable}
              />
              <button type="submit" disabled={!refinable || !refinement.trim()} aria-label="Применить изменение">
                <PaperPlaneTilt aria-hidden size={19} weight="fill" />
              </button>
            </div>
            <p>{refinable ? 'Ctrl + Enter тоже отправляет пожелание' : 'Доработка откроется после проверенной версии'}</p>
          </form>
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
              <CheckCircle aria-hidden size={19} weight={controller.snapshot?.quality_status === 'verified' ? 'fill' : 'regular'} />
              <span>{controller.snapshot?.quality_status === 'verified' ? 'Проверено' : 'Черновик'}</span>
            </div>
          </div>
          <StudioPreview
            runId={controller.runId}
            revision={artifact?.revision ?? null}
            artDirection={artifact?.art_direction ?? ''}
            qualityStatus={controller.snapshot?.quality_status ?? 'pending'}
            viewport={viewport}
            onViewportChange={setViewport}
          />
          <div className="studio-workspace__footer">
            <Code aria-hidden size={18} />
            <span>Preview изолирован: скрипты разрешены только внутри sandbox, сеть заблокирована серверным CSP.</span>
          </div>
        </motion.section>
      </main>
    </div>
  );
}
