import { useEffect, useId, useRef, useState } from 'react';

import {
  CONTACT_CONFIG,
  feedbackTopics,
  isFeedbackReady,
  type FeedbackTopicId,
} from './contact';
import {
  createFeedbackIdempotencyKey,
  FeedbackApiError,
  fetchFeedbackSession,
  submitFeedback,
  type FeedbackSource,
  type FeedbackSubmission,
} from './feedbackApi';
import { marketingHref } from './marketing';

export type FeedbackComposerProps = {
  source: FeedbackSource;
  csrfToken?: string | null;
  className?: string;
};

type ComposerStatus = 'idle' | 'sending' | 'success' | 'error';

type FeedbackSession = {
  csrfToken: string;
  consentVersion: string;
  messageMaxLength: number;
};

type FeedbackAttempt = {
  fingerprint: string;
  source: FeedbackSource;
  topic: FeedbackTopicId;
  normalizedMessage: string;
  csrfToken: string;
  consentVersion: string;
  messageMaxLength: number;
  payload: FeedbackSubmission;
  idempotencyKey: string;
};

const MAX_MESSAGE_LENGTH = 4000;
const SUCCESS_MESSAGE = 'Спасибо. Сообщение сохранено и поможет улучшать Kaigo.';
const ERROR_MESSAGE = 'Не удалось отправить. Ваш текст остался в форме.';
const BAD_REQUEST_MESSAGE = 'Не удалось принять сообщение. Проверьте текст и попробуйте ещё раз.';
const FORBIDDEN_MESSAGE = 'Сессия обратной связи устарела. Нажмите «Повторить», чтобы получить новый токен.';
const CONFLICT_MESSAGE = 'Не удалось подтвердить попытку отправки. Нажмите «Повторить» для новой попытки.';
const RATE_LIMIT_LATER_MESSAGE = 'Слишком много запросов. Повторите позже.';

function feedbackFingerprint(input: {
  source: FeedbackSource;
  topic: FeedbackTopicId;
  trimmedMessage: string;
  consentVersion: string;
}): string {
  return JSON.stringify(input);
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
  const [errorMessage, setErrorMessage] = useState(ERROR_MESSAGE);
  const [retryAfterSeconds, setRetryAfterSeconds] = useState<number | null>(null);
  const [retryBlocked, setRetryBlocked] = useState(false);
  const attemptRef = useRef<FeedbackAttempt | null>(null);
  const sessionRef = useRef<FeedbackSession | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);
  const messageId = useId();

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortControllerRef.current?.abort();
      abortControllerRef.current = null;
    };
  }, []);

  const isSending = status === 'sending';
  const canSubmit = isFeedbackReady(message) && !isSending && !retryBlocked;
  const formClassName = ['feedback-composer', className].filter(Boolean).join(' ');

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) return;

    const normalizedMessage = message.trim();
    const controller = new AbortController();
    abortControllerRef.current = controller;
    setErrorMessage(ERROR_MESSAGE);
    setStatus('sending');

    try {
      const previousAttempt = attemptRef.current;
      const previousFingerprint = previousAttempt
        ? feedbackFingerprint({
          source,
          topic,
          trimmedMessage: normalizedMessage,
          consentVersion: previousAttempt.consentVersion,
        })
        : null;
      let attempt: FeedbackAttempt;

      if (previousAttempt && previousFingerprint === previousAttempt.fingerprint) {
        attempt = previousAttempt;
      } else {
        let session: FeedbackSession;
        if (csrfToken !== null && csrfToken !== undefined) {
          session = {
            csrfToken,
            consentVersion: CONTACT_CONFIG.consentDocumentVersion,
            messageMaxLength: MAX_MESSAGE_LENGTH,
          };
        } else {
          const cachedSession = sessionRef.current;
          session = cachedSession ?? await fetchFeedbackSession(controller.signal);
          if (!cachedSession) sessionRef.current = session;
        }

        const effectiveMaxLength = Math.min(MAX_MESSAGE_LENGTH, session.messageMaxLength);
        const fingerprint = feedbackFingerprint({
          source,
          topic,
          trimmedMessage: normalizedMessage,
          consentVersion: session.consentVersion,
        });
        const payload: FeedbackSubmission = {
          topic,
          message: normalizedMessage,
          source,
          consent: { version: session.consentVersion, accepted: true },
        };
        attempt = {
          fingerprint,
          source,
          topic,
          normalizedMessage,
          csrfToken: session.csrfToken,
          consentVersion: session.consentVersion,
          messageMaxLength: effectiveMaxLength,
          payload,
          idempotencyKey: previousAttempt?.fingerprint === fingerprint
            ? previousAttempt.idempotencyKey
            : createFeedbackIdempotencyKey(),
        };
        attemptRef.current = attempt;
      }

      setMessageMaxLength(attempt.messageMaxLength);

      if (attempt.normalizedMessage.length > attempt.messageMaxLength) {
        setErrorMessage(`Сократите сообщение до ${attempt.messageMaxLength} символов.`);
        setStatus('error');
        return;
      }

      await submitFeedback({
        payload: attempt.payload,
        csrfToken: attempt.csrfToken,
        idempotencyKey: attempt.idempotencyKey,
        signal: controller.signal,
      });

      if (!mountedRef.current) return;
      attemptRef.current = null;
      setMessage('');
      setRetryAfterSeconds(null);
      setRetryBlocked(false);
      setStatus('success');
    } catch (error) {
      if (!mountedRef.current || isAbortError(error)) return;
      setRetryAfterSeconds(null);
      setRetryBlocked(false);
      if (error instanceof FeedbackApiError) {
        if (error.status === 429) {
          setRetryAfterSeconds(error.retryAfter);
          setRetryBlocked(true);
          setErrorMessage(error.retryAfter === null
            ? RATE_LIMIT_LATER_MESSAGE
            : `Слишком много запросов. Повторите через ${error.retryAfter} с.`);
        } else if (error.status === 400) {
          attemptRef.current = null;
          setErrorMessage(BAD_REQUEST_MESSAGE);
        } else if (error.status === 403) {
          attemptRef.current = null;
          sessionRef.current = null;
          setErrorMessage(FORBIDDEN_MESSAGE);
        } else if (error.status === 409) {
          attemptRef.current = null;
          setErrorMessage(CONFLICT_MESSAGE);
        } else {
          setErrorMessage(ERROR_MESSAGE);
        }
      } else {
        setErrorMessage(ERROR_MESSAGE);
      }
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

  const actionLabel = isSending
    ? 'Отправляем…'
    : status === 'error'
      ? retryBlocked
        ? retryAfterSeconds === null ? 'Повторить позже' : `Повторить через ${retryAfterSeconds} с`
        : 'Повторить'
      : 'Отправить';

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

      <label className="feedback-composer__message" htmlFor={messageId}>
        <span>Сообщение</span>
        <textarea
          id={messageId}
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
            {actionLabel}
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
          {errorMessage}
        </p>
      )}
    </form>
  );
}
