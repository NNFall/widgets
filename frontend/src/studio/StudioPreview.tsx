import { CheckCircle, Desktop, DeviceMobile, Eye, Sparkle } from '@phosphor-icons/react';
import { useEffect, useLayoutEffect, useMemo, useRef } from 'react';

import {
  BuilderApiError,
  builderUrl,
  saasPreviewUrl,
  sendPreviewChat,
  sendSaasPreviewChat,
} from './api';
import type { PreviewViewport } from './types';

const REQUEST_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{7,95}$/;

function createPreviewChannel() {
  const values = new Uint8Array(18);
  crypto.getRandomValues(values);
  return Array.from(values, (value) => value.toString(16).padStart(2, '0')).join('');
}

interface StudioPreviewProps {
  runId: string | null;
  revision: number | null;
  artDirection: string;
  qualityStatus: string;
  viewport: PreviewViewport;
  onViewportChange: (viewport: PreviewViewport) => void;
  projectMode?: boolean;
  compactProjectHeader?: boolean;
  versionNumber?: number | null;
  csrfToken?: string | null;
}

export function StudioPreview({
  runId,
  revision,
  artDirection,
  qualityStatus,
  viewport,
  onViewportChange,
  projectMode = false,
  compactProjectHeader = false,
  versionNumber = null,
  csrfToken = null,
}: StudioPreviewProps) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const requestsRef = useRef(new Set<string>());
  const channel = useMemo(() => createPreviewChannel(), [runId, revision]);
  const previewUrl = runId && revision
    ? projectMode
      ? saasPreviewUrl(runId, revision, channel)
      : builderUrl(`api/runs/${encodeURIComponent(runId)}/preview?revision=${revision}&channel=${encodeURIComponent(channel)}`)
    : '';

  useEffect(() => {
    requestsRef.current.clear();
  }, [channel]);

  useLayoutEffect(() => {
    if (!runId || !revision) return;
    const handleMessage = async (event: MessageEvent) => {
      const data = event.data as Record<string, unknown> | null;
      if (
        event.source !== iframeRef.current?.contentWindow
        || !data
        || data.source !== 'kaigo-builder-preview'
        || data.version !== 2
        || data.channel_id !== channel
        || data.revision !== revision
      ) return;
      if (data.type === 'rendered') return;
      if (
        data.type !== 'chat.request'
        || typeof data.request_id !== 'string'
        || !REQUEST_PATTERN.test(data.request_id)
        || typeof data.text !== 'string'
        || data.text.trim().length < 1
        || data.text.length > 1_000
        || requestsRef.current.has(data.request_id)
      ) return;

      const requestId = data.request_id;
      requestsRef.current.add(requestId);
      const respond = (payload: Record<string, unknown>) => {
        iframeRef.current?.contentWindow?.postMessage({
          source: 'kaigo-builder-parent',
          version: 2,
          channel_id: channel,
          revision,
          request_id: requestId,
          ...payload,
        }, '*');
      };
      try {
        if (projectMode && !csrfToken) {
          throw new BuilderApiError('authentication_required', {
            status: 401,
            code: 'authentication_required',
            raw: 'authentication_required',
          });
        }
        const payload = {
          request_id: requestId,
          message: data.text.trim(),
          revision,
        };
        const response = projectMode
          ? await sendSaasPreviewChat(runId, csrfToken!, payload)
          : await sendPreviewChat(runId, payload);
        if (response.request_id !== requestId || !response.reply || response.reply.length > 4_000) {
          throw new Error('Некорректный ответ chat bridge');
        }
        respond({ type: 'chat.response', text: response.reply });
      } catch (caught) {
        const apiError = caught instanceof BuilderApiError ? caught : null;
        respond({
          type: 'chat.error',
          code: apiError?.code ?? 'chat_network_error',
          message: apiError?.raw ?? 'Связь прервалась. Текст сохранён.',
          retryable: apiError?.retryable ?? false,
        });
      } finally {
        requestsRef.current.delete(requestId);
      }
    };
    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [channel, csrfToken, projectMode, revision, runId]);

  const qualityLabel = qualityStatus === 'accepted'
    ? 'Готово'
    : qualityStatus === 'verified'
    ? 'Проверено'
    : qualityStatus === 'pending'
      ? 'Проверяется'
      : 'Нужна доработка';
  const friendlyPreviewMessage = qualityStatus === 'accepted' || qualityStatus === 'verified'
    ? 'Виджет готов к просмотру. Проверьте его на компьютере и телефоне.'
    : qualityStatus === 'pending'
      ? 'Kaigo собирает новую версию. Предпросмотр обновится автоматически.'
      : 'Этой версии нужна доработка перед публикацией.';
  const compactQualityLabel = qualityStatus === 'accepted' || qualityStatus === 'verified'
    ? 'Проверено'
    : qualityStatus === 'pending'
      ? 'Генерация'
      : 'Нужна проверка';
  const qualityReady = qualityStatus === 'accepted' || qualityStatus === 'verified';

  return (
    <section className="studio-preview" aria-labelledby="studio-preview-title">
      <div className={`studio-preview__head${compactProjectHeader ? ' studio-preview__head--compact' : ''}`}>
        <div className={compactProjectHeader ? 'studio-preview__title-row' : undefined}>
          {!compactProjectHeader && <p className="studio-kicker">Рабочее полотно</p>}
          <h2 id="studio-preview-title">Предпросмотр</h2>
          {compactProjectHeader && (
            <>
              <span className="studio-preview__version">Версия {versionNumber ?? '—'}</span>
              <span className="studio-preview__quality" data-quality={qualityStatus} aria-live="polite">
                <CheckCircle aria-hidden size={17} weight={qualityReady ? 'fill' : 'regular'} />
                {compactQualityLabel}
              </span>
            </>
          )}
        </div>
        <div className="studio-preview__switcher" aria-label="Размер предпросмотра">
          <button type="button" aria-pressed={viewport === 'desktop'} onClick={() => onViewportChange('desktop')}>
            <Desktop aria-hidden size={18} weight="regular" /> На компьютере
          </button>
          <button type="button" aria-pressed={viewport === 'mobile'} onClick={() => onViewportChange('mobile')}>
            <DeviceMobile aria-hidden size={18} weight="regular" /> На телефоне
          </button>
        </div>
      </div>
      {!compactProjectHeader && (
        <div className="studio-preview__meta" aria-live="polite">
          <Sparkle aria-hidden size={20} weight="fill" />
          <div>
            <span>{projectMode ? 'Состояние виджета' : 'Визуальная концепция'}</span>
            <p>{projectMode ? friendlyPreviewMessage : artDirection || 'Появится после первой собранной версии'}</p>
          </div>
          <strong data-quality={qualityStatus}>{qualityLabel}</strong>
        </div>
      )}
      <div className="studio-preview__canvas" data-viewport={viewport} data-testid="studio-preview-canvas">
        <div className="studio-preview__grid" aria-hidden />
        {previewUrl ? (
          <div className="studio-preview__device">
            <iframe
              ref={iframeRef}
              src={previewUrl}
              sandbox="allow-scripts"
              referrerPolicy="no-referrer"
              title="Предпросмотр AI-сотрудника Kaigo"
            />
          </div>
        ) : (
          <div className="studio-preview__empty">
            <span><Eye aria-hidden size={28} weight="regular" /></span>
            <strong>Первый вариант появится здесь</strong>
            <p>Kaigo покажет каждую сохранённую версию здесь без перезагрузки страницы.</p>
          </div>
        )}
      </div>
    </section>
  );
}
