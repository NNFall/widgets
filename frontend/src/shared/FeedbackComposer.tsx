import { useEffect, useRef, useState } from 'react';

import {
  CONTACT_CONFIG,
  feedbackTopics,
  isFeedbackReady,
  type FeedbackTopicId,
} from './contact';
import {
  createFeedbackIdempotencyKey,
  fetchFeedbackSession,
  submitFeedback,
  type FeedbackSource,
} from './feedbackApi';
import { marketingHref } from './marketing';

export type FeedbackComposerProps = {
  source: FeedbackSource;
  csrfToken?: string | null;
  className?: string;
};

type ComposerStatus = 'idle' | 'sending' | 'success' | 'error';

type IdempotencyState = {
  fingerprint: string;
  key: string;
};

const MAX_MESSAGE_LENGTH = 4000;
const SUCCESS_MESSAGE = 'Спасибо. Сообщение сохранено и поможет улучшать Kaigo.';
const ERROR_MESSAGE = 'Не удалось отправить. Ваш текст остался в форме.';

function feedbackFingerprint(topic: FeedbackTopicId, message: string, source: FeedbackSource): string {
  return JSON.stringify({ source, topic, trimmedMessage: message.trim() });
}

function isAbortError(error: unknown): boolean {
  return typeof error === 'object'
    && error !== null
    && 'name' in error
    && error.name === 'AbortError';
}

export function FeedbackComposer({ source, csrfToken = null, className = '' }: FeedbackComposerProps) {
  const [topic, setTopic] = useState<FeedbackTopicId>('question');
  const [message, setMessage] = useState('');
  const [messageMaxLength, setMessageMaxLength] = useState(MAX_MESSAGE_LENGTH);
  const [status, setStatus] = useState<ComposerStatus>('idle');
  const idempotencyRef = useRef<IdempotencyState | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortControllerRef.current?.abort();
      abortControllerRef.current = null;
    };
  }, []);

  const isSending = status === 'sending';
  const canSubmit = isFeedbackReady(message) && !isSending;
  const formClassName = ['feedback-composer', className].filter(Boolean).join(' ');

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) return;

    const normalizedMessage = message.trim();
    const fingerprint = feedbackFingerprint(topic, normalizedMessage, source);
    const idempotencyKey = idempotencyRef.current?.fingerprint === fingerprint
      ? idempotencyRef.current.key
      : createFeedbackIdempotencyKey();
    idempotencyRef.current = { fingerprint, key: idempotencyKey };

    const controller = new AbortController();
    abortControllerRef.current = controller;
    setStatus('sending');

    try {
      const session = csrfToken === null || csrfToken === undefined
        ? await fetchFeedbackSession(controller.signal)
        : null;
      const token = session?.csrfToken ?? csrfToken;
      const consentVersion = session?.consentVersion ?? CONTACT_CONFIG.consentDocumentVersion;

      if (!token) {
        throw new Error('feedback_csrf_missing');
      }

      if (session) {
        setMessageMaxLength(session.messageMaxLength);
      }

      await submitFeedback({
        payload: {
          topic,
          message: normalizedMessage,
          source,
          consent: { version: consentVersion, accepted: true },
        },
        csrfToken: token,
        idempotencyKey,
        signal: controller.signal,
      });

      if (!mountedRef.current) return;
      idempotencyRef.current = null;
      setMessage('');
      setStatus('success');
    } catch (error) {
      if (!mountedRef.current || isAbortError(error)) return;
      setStatus('error');
    } finally {
      if (abortControllerRef.current === controller) {
        abortControllerRef.current = null;
      }
    }
  }

  function handleComposeAgain() {
    setStatus('idle');
  }

  return (
    <form
      className={formClassName}
      onSubmit={handleSubmit}
      aria-busy={isSending}
    >
      <fieldset className="feedback-composer__topics" disabled={isSending}>
        <legend>О чём хотите написать?</legend>
        <div className="feedback-topic-grid">
          {feedbackTopics.map((feedbackTopic) => (
            <label className="feedback-topic" key={feedbackTopic.id}>
              <input
                type="radio"
                name="feedback-topic"
                value={feedbackTopic.id}
                checked={topic === feedbackTopic.id}
                onChange={() => setTopic(feedbackTopic.id)}
              />
              <span>{feedbackTopic.label}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <label className="feedback-composer__message" htmlFor="feedback-message">
        <span>Сообщение</span>
        <textarea
          id="feedback-message"
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          placeholder="Например: хочу уточнить, как проверить виджет на сайте"
          rows={6}
          maxLength={messageMaxLength}
          required
          disabled={isSending}
        />
      </label>

      <p className="feedback-composer__consent">
        Нажимая «Отправить», вы подтверждаете согласие на обработку сообщения в соответствии с{' '}
        <a
          href={marketingHref(CONTACT_CONFIG.consentDocumentPath)}
          aria-label="текстом согласия на обработку персональных данных"
        >
          текстом согласия на обработку персональных данных.
        </a>
      </p>

      <div className="feedback-composer__action-row">
        {status === 'success' ? (
          <button
            className="primary-button feedback-composer__action"
            type="button"
            onClick={handleComposeAgain}
          >
            Отправить ещё
          </button>
        ) : (
          <button
            className="primary-button feedback-composer__action"
            type="submit"
            disabled={!canSubmit}
          >
            {isSending ? 'Отправляем…' : status === 'error' ? 'Повторить' : 'Отправить'}
          </button>
        )}
      </div>

      {status === 'success' && (
        <p className="feedback-composer__result feedback-composer__status" role="status" aria-live="polite">
          {SUCCESS_MESSAGE}
        </p>
      )}
      {status === 'error' && (
        <p className="feedback-composer__result feedback-composer__error" role="alert">
          {ERROR_MESSAGE}
        </p>
      )}
    </form>
  );
}
