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
const STUDIO_FORBIDDEN_MESSAGE = 'Сессия Studio устарела. Обновите страницу и попробуйте снова.';
const CONFLICT_MESSAGE = 'Не удалось подтвердить попытку отправки. Нажмите «Повторить» для новой попытки.';
const RATE_LIMIT_LATER_MESSAGE = 'Слишком много запросов. Повторите позже.';
const RATE_LIMIT_READY_MESSAGE = 'Теперь можно повторить отправку.';
const RATE_LIMIT_FALLBACK_SECONDS = 60;

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
  const [rateLimitDeadlineMs, setRateLimitDeadlineMs] = useState<number | null>(null);
  const [rateLimitNowMs, setRateLimitNowMs] = useState(() => Date.now());
  const [retryBlocked, setRetryBlocked] = useState(false);
  const [rateLimitReady, setRateLimitReady] = useState(false);
  const [requiresStudioRefresh, setRequiresStudioRefresh] = useState(false);
  const attemptRef = useRef<FeedbackAttempt | null>(null);
  const sessionRef = useRef<FeedbackSession | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const rateLimitTimerRef = useRef<number | null>(null);
  const mountedRef = useRef(true);
  const messageId = useId();

  function clearRateLimitTimer() {
    if (rateLimitTimerRef.current !== null) {
      window.clearInterval(rateLimitTimerRef.current);
      rateLimitTimerRef.current = null;
    }
  }

  function startRateLimitCountdown(seconds: number | null) {
    clearRateLimitTimer();
    const delaySeconds = seconds ?? RATE_LIMIT_FALLBACK_SECONDS;
    const now = Date.now();
    if (delaySeconds <= 0) {
      setRateLimitDeadlineMs(null);
      setRateLimitNowMs(now);
      setRetryBlocked(false);
      setRateLimitReady(true);
      setErrorMessage(RATE_LIMIT_LATER_MESSAGE);
      return;
    }

    const deadline = now + delaySeconds * 1000;
    setRateLimitDeadlineMs(deadline);
    setRateLimitNowMs(now);
    setRetryBlocked(true);
    setRateLimitReady(false);
    setErrorMessage(RATE_LIMIT_LATER_MESSAGE);
    rateLimitTimerRef.current = window.setInterval(() => {
      const currentTime = Date.now();
      setRateLimitNowMs(currentTime);
      if (currentTime >= deadline) {
        clearRateLimitTimer();
        setRateLimitDeadlineMs(null);
        setRetryBlocked(false);
        setRateLimitReady(true);
      }
    }, 1000);
  }

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortControllerRef.current?.abort();
      abortControllerRef.current = null;
      clearRateLimitTimer();
    };
  }, []);

  useEffect(() => {
    if (csrfToken !== null && csrfToken !== undefined && attemptRef.current) {
      attemptRef.current = {
        ...attemptRef.current,
        csrfToken,
      };
    }
  }, [csrfToken]);

  const isSending = status === 'sending';
  const rateLimitRemainingSeconds = rateLimitDeadlineMs === null
    ? null
    : Math.max(0, Math.ceil((rateLimitDeadlineMs - rateLimitNowMs) / 1000));
  const canSubmit = isFeedbackReady(message) && !isSending && !retryBlocked && !requiresStudioRefresh;
  const formClassName = ['feedback-composer', className].filter(Boolean).join(' ');

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) return;

    const normalizedMessage = message.trim();
    const controller = new AbortController();
    abortControllerRef.current = controller;
    setErrorMessage(ERROR_MESSAGE);
    setRateLimitReady(false);
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
      clearRateLimitTimer();
      setRateLimitDeadlineMs(null);
      setRateLimitNowMs(Date.now());
      setRetryBlocked(false);
      setRateLimitReady(false);
      setRequiresStudioRefresh(false);
      setStatus('success');
    } catch (error) {
      if (!mountedRef.current || isAbortError(error)) return;
      if (error instanceof FeedbackApiError) {
        if (error.status === 429) {
          startRateLimitCountdown(error.retryAfter);
        } else if (error.status === 400 || error.status === 422) {
          clearRateLimitTimer();
          setRateLimitDeadlineMs(null);
          setRateLimitNowMs(Date.now());
          setRetryBlocked(false);
          setRateLimitReady(false);
          attemptRef.current = null;
          setErrorMessage(BAD_REQUEST_MESSAGE);
        } else if (error.status === 403) {
          clearRateLimitTimer();
          setRateLimitDeadlineMs(null);
          setRateLimitNowMs(Date.now());
          setRetryBlocked(false);
          setRateLimitReady(false);
          attemptRef.current = null;
          sessionRef.current = null;
          if (csrfToken !== null && csrfToken !== undefined) {
            setRequiresStudioRefresh(true);
            setErrorMessage(STUDIO_FORBIDDEN_MESSAGE);
          } else {
            setRequiresStudioRefresh(false);
            setErrorMessage(FORBIDDEN_MESSAGE);
          }
        } else if (error.status === 409) {
          clearRateLimitTimer();
          setRateLimitDeadlineMs(null);
          setRateLimitNowMs(Date.now());
          setRetryBlocked(false);
          setRateLimitReady(false);
          attemptRef.current = null;
          setErrorMessage(CONFLICT_MESSAGE);
        } else {
          clearRateLimitTimer();
          setRateLimitDeadlineMs(null);
          setRateLimitNowMs(Date.now());
          setRetryBlocked(false);
          setRateLimitReady(false);
          setErrorMessage(ERROR_MESSAGE);
        }
      } else {
        clearRateLimitTimer();
        setRateLimitDeadlineMs(null);
        setRateLimitNowMs(Date.now());
        setRetryBlocked(false);
        setRateLimitReady(false);
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
        ? rateLimitRemainingSeconds === null ? 'Повторить позже' : `Повторить через ${rateLimitRemainingSeconds} с`
        : 'Повторить'
      : 'Отправить';
  const refreshHref = typeof window === 'undefined' ? '/' : window.location.href;

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
        ) : status === 'error' && requiresStudioRefresh ? (
          <a className="primary-button feedback-composer__action" href={refreshHref}>
            Обновить страницу
          </a>
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
        <>
          {!rateLimitReady && (
            <p className="feedback-composer__result feedback-composer__error" role="alert">
              {errorMessage}
            </p>
          )}
          {retryBlocked && rateLimitRemainingSeconds !== null && (
            <p className="feedback-composer__result feedback-composer__rate-limit-countdown" aria-live="off">
              Повторите через {rateLimitRemainingSeconds} с.
            </p>
          )}
          {rateLimitReady && (
            <p className="feedback-composer__result feedback-composer__rate-limit-ready" role="status" aria-live="polite">
              {RATE_LIMIT_READY_MESSAGE}
            </p>
          )}
        </>
      )}
    </form>
  );
}
