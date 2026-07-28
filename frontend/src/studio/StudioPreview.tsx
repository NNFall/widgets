import { Desktop, DeviceMobile, Eye, Sparkle } from '@phosphor-icons/react';
import { useEffect, useMemo, useRef } from 'react';

import { BuilderApiError, builderUrl, sendPreviewChat } from './api';
import type { PreviewViewport, WidgetArtifact } from './types';

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
  artifact?: WidgetArtifact | null;
}

function artifactDocument(artifact: WidgetArtifact) {
  const style = artifact.css.replace(/<\/style/gi, '<\\/style');
  const script = artifact.javascript.replace(/<\/script/gi, '<\\/script');
  const policy = "default-src 'none'; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; img-src data: blob:; style-src 'unsafe-inline'; script-src 'unsafe-inline'";
  return `<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${policy}"><meta name="viewport" content="width=device-width,initial-scale=1"><style>${style}</style></head><body>${artifact.body_html}<script>${script}</script></body></html>`;
}

export function StudioPreview({
  runId,
  revision,
  artDirection,
  qualityStatus,
  viewport,
  onViewportChange,
  artifact = null,
}: StudioPreviewProps) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const requestsRef = useRef(new Set<string>());
  const channel = useMemo(() => createPreviewChannel(), [runId, revision]);
  const previewUrl = runId && revision
    ? builderUrl(`api/runs/${encodeURIComponent(runId)}/preview?revision=${revision}&channel=${encodeURIComponent(channel)}`)
    : '';
  const previewDocument = useMemo(() => artifact ? artifactDocument(artifact) : undefined, [artifact]);

  useEffect(() => {
    requestsRef.current.clear();
  }, [channel]);

  useEffect(() => {
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
        const response = await sendPreviewChat(runId, {
          request_id: requestId,
          message: data.text.trim(),
          revision,
        });
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
  }, [channel, revision, runId]);

  const qualityLabel = qualityStatus === 'verified'
    ? 'Проверено'
    : qualityStatus === 'pending'
      ? 'Проверяется'
      : 'Нужна доработка';

  return (
    <section className="studio-preview" aria-labelledby="studio-preview-title">
      <div className="studio-preview__head">
        <div>
          <p className="studio-kicker">Рабочее полотно</p>
          <h2 id="studio-preview-title">Предпросмотр</h2>
        </div>
        <div className="studio-preview__switcher" aria-label="Размер предпросмотра">
          <button type="button" aria-pressed={viewport === 'desktop'} onClick={() => onViewportChange('desktop')}>
            <Desktop aria-hidden size={18} weight="regular" /> Desktop
          </button>
          <button type="button" aria-pressed={viewport === 'mobile'} onClick={() => onViewportChange('mobile')}>
            <DeviceMobile aria-hidden size={18} weight="regular" /> Mobile
          </button>
        </div>
      </div>
      <div className="studio-preview__meta" aria-live="polite">
        <Sparkle aria-hidden size={20} weight="fill" />
        <div>
          <span>Визуальная концепция</span>
          <p>{artDirection || 'Появится после первой собранной версии'}</p>
        </div>
        <strong data-quality={qualityStatus}>{qualityLabel}</strong>
      </div>
      <div className="studio-preview__canvas" data-viewport={viewport} data-testid="studio-preview-canvas">
        <div className="studio-preview__grid" aria-hidden />
        {previewUrl || previewDocument ? (
          <div className="studio-preview__device">
            <iframe
              ref={iframeRef}
              src={previewDocument ? undefined : previewUrl}
              srcDoc={previewDocument}
              sandbox="allow-scripts"
              referrerPolicy="no-referrer"
              title={previewDocument ? 'Предпросмотр виджета' : 'Предпросмотр AI-сотрудника Kaigo'}
            />
          </div>
        ) : (
          <div className="studio-preview__empty">
            <span><Eye aria-hidden size={28} weight="regular" /></span>
            <strong>Первый вариант появится здесь</strong>
            <p>Kaigo покажет каждую принятую ревизию без перезагрузки страницы.</p>
          </div>
        )}
      </div>
    </section>
  );
}
