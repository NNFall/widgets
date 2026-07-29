import { ArrowRight, CheckCircle, Clock, Sparkle } from '@phosphor-icons/react';
import { useEffect, useRef, useState } from 'react';

import {
  BuilderApiError,
  createBillingCheckout,
  getBillingPayment,
  getPendingBillingPayment,
  getBillingSubscription,
  getProjectPublication,
  publishProject,
  rollbackPublication,
  resumeBillingPayment,
  type PublicationRelease,
} from './api';

const DEFAULT_PLAN_CODE = 'starter_monthly';
const DEFAULT_POLL_INTERVAL_MS = 2_000;
const DEFAULT_MAX_POLL_ATTEMPTS = 150;

type UpgradeState =
  | 'checking'
  | 'idle'
  | 'creating'
  | 'pending'
  | 'active'
  | 'checkout_error'
  | 'recovery_error';

interface UpgradeGateProps {
  csrfToken: string | null;
  projectId?: string;
  artifactId?: string;
  revision?: number;
  pollIntervalMs?: number;
  maxPollAttempts?: number;
}

type RollbackRelease = Pick<PublicationRelease, 'release_id' | 'revision'>;

function safeCheckoutUrl(value: string) {
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== 'https:') return null;
    const hostname = parsed.hostname.toLowerCase().replace(/\.$/, '');
    const allowed = ['yookassa.ru', 'yoomoney.ru'].some(
      (domain) => hostname === domain || hostname.endsWith(`.${domain}`),
    );
    return allowed ? parsed.toString() : null;
  } catch {
    return null;
  }
}

function checkoutKey() {
  return crypto.randomUUID();
}

export function UpgradeGate({
  csrfToken,
  projectId = '',
  artifactId = '',
  revision = 0,
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
  maxPollAttempts = DEFAULT_MAX_POLL_ATTEMPTS,
}: UpgradeGateProps) {
  const [state, setState] = useState<UpgradeState>(csrfToken ? 'checking' : 'idle');
  const [paymentId, setPaymentId] = useState<string | null>(null);
  const [checkoutUrl, setCheckoutUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [recoveryAttempt, setRecoveryAttempt] = useState(0);
  const [allowedDomains, setAllowedDomains] = useState('');
  const [publication, setPublication] = useState<PublicationRelease | null>(null);
  const [priorReleases, setPriorReleases] = useState<RollbackRelease[]>([]);
  const [publicationPending, setPublicationPending] = useState(false);
  const [publicationError, setPublicationError] = useState<string | null>(null);
  const idempotencyKeyRef = useRef<string | null>(null);

  useEffect(() => {
    if (!csrfToken) {
      setState('idle');
      return undefined;
    }
    const abort = new AbortController();
    setState('checking');
    setError(null);
    void (async () => {
      try {
        const { subscription } = await getBillingSubscription(abort.signal);
        if (abort.signal.aborted) return;
        if (subscription?.status === 'active') {
          setState('active');
          return;
        }
        const pending = await getPendingBillingPayment(abort.signal);
        if (abort.signal.aborted) return;
        if (pending.payment === null && pending.checkout_url === null) {
          setPaymentId(null);
          setCheckoutUrl(null);
          setState('idle');
          return;
        }
        let recoveredPayment = pending.payment;
        let recoveredCheckoutUrl = pending.checkout_url;
        if (
          (recoveredPayment?.status === 'creating' || recoveredPayment?.status === 'failed')
          && recoveredCheckoutUrl === null
        ) {
          const resumed = await resumeBillingPayment(recoveredPayment.id, csrfToken);
          if (
            abort.signal.aborted
            || resumed.created
            || resumed.payment.id !== recoveredPayment.id
          ) {
            if (abort.signal.aborted) return;
            throw new Error('invalid_resumed_checkout');
          }
          recoveredPayment = resumed.payment;
          recoveredCheckoutUrl = resumed.checkout_url;
        }
        if (recoveredPayment === null || recoveredCheckoutUrl === null) {
          throw new Error('invalid_pending_checkout');
        }
        const safeUrl = safeCheckoutUrl(recoveredCheckoutUrl);
        if (!safeUrl) throw new Error('invalid_pending_checkout_url');
        setPaymentId(recoveredPayment.id);
        setCheckoutUrl(safeUrl);
        setState('pending');
      } catch {
        if (abort.signal.aborted) return;
        setState('recovery_error');
        setError('Не удалось проверить тариф и незавершённую оплату. Повторите проверку.');
      }
    })();
    return () => abort.abort();
  }, [csrfToken, recoveryAttempt]);

  useEffect(() => {
    if (!paymentId || state !== 'pending') return undefined;

    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | null = null;
    let attempts = 0;

    const schedule = () => {
      if (abort.signal.aborted) return;
      if (attempts >= maxPollAttempts) {
        setState('checkout_error');
        setError('Подтверждение оплаты заняло больше обычного. Проверьте статус чуть позже.');
        return;
      }
      timer = setTimeout(checkStatus, pollIntervalMs);
    };

    const checkStatus = async () => {
      timer = null;
      if (abort.signal.aborted) return;
      attempts += 1;
      try {
        const { payment } = await getBillingPayment(paymentId, abort.signal);
        if (abort.signal.aborted) return;
        if (payment.status === 'cancelled' || payment.status === 'failed') {
          idempotencyKeyRef.current = null;
          setState('checkout_error');
          setError('Оплата не завершена. Можно открыть платёжную страницу и попробовать ещё раз.');
          return;
        }
        if (payment.status === 'succeeded') {
          const { subscription } = await getBillingSubscription(abort.signal);
          if (abort.signal.aborted) return;
          if (subscription?.status === 'active') {
            setState('active');
            setError(null);
            return;
          }
        }
        schedule();
      } catch (caught) {
        if (abort.signal.aborted) return;
        if (
          caught instanceof BuilderApiError
          && [401, 403, 404].includes(caught.status)
        ) {
          setState('recovery_error');
          setError('Не удалось проверить оплату. Обновите страницу и войдите в аккаунт снова.');
          return;
        }
        schedule();
      }
    };

    schedule();
    return () => {
      abort.abort();
      if (timer) clearTimeout(timer);
    };
  }, [maxPollAttempts, paymentId, pollIntervalMs, state]);

  useEffect(() => {
    if (state !== 'active' || !projectId) return undefined;
    const abort = new AbortController();
    setPublicationPending(true);
    setPublicationError(null);
    void getProjectPublication(projectId, abort.signal)
      .then(({ publication: restored }) => {
        if (abort.signal.aborted) return;
        if (restored === null) {
          setPublication(null);
          setPriorReleases([]);
          return;
        }
        const activeRelease = restored.active_release;
        setPublication({
          publication_id: restored.publication_id,
          release_id: activeRelease.release_id,
          artifact_id: activeRelease.artifact_id,
          stable_key: restored.stable_key,
          revision: activeRelease.revision,
          allowed_domains: restored.allowed_domains,
          checksum: activeRelease.checksum,
          embed_url: restored.embed_url,
          runtime_url: restored.runtime_url,
        });
        setAllowedDomains(restored.allowed_domains.join('\n'));
        const directPrevious = restored.releases.find(
          ({ release_id }) => release_id === activeRelease.previous_release_id,
        );
        setPriorReleases([
          ...restored.releases.filter(({ release_id }) => (
            release_id !== activeRelease.release_id
            && release_id !== directPrevious?.release_id
          )),
          ...(directPrevious ? [directPrevious] : []),
        ]);
      })
      .catch(() => {
        if (!abort.signal.aborted) {
          setPublicationError('Не удалось восстановить историю публикации. Повторная публикация остаётся доступна.');
        }
      })
      .finally(() => {
        if (!abort.signal.aborted) setPublicationPending(false);
      });
    return () => abort.abort();
  }, [projectId, state]);

  const startCheckout = async () => {
    if (!csrfToken || state === 'creating' || state === 'pending' || state === 'active') return;
    const paymentWindow = window.open('about:blank', '_blank');
    if (paymentWindow) paymentWindow.opener = null;
    setState('creating');
    setError(null);
    setCheckoutUrl(null);
    const idempotencyKey = idempotencyKeyRef.current ?? checkoutKey();
    idempotencyKeyRef.current = idempotencyKey;

    try {
      const checkout = await createBillingCheckout(
        DEFAULT_PLAN_CODE,
        csrfToken,
        idempotencyKey,
      );
      const safeUrl = safeCheckoutUrl(checkout.checkout_url);
      if (!safeUrl) throw new Error('invalid_checkout_url');
      if (paymentWindow && !paymentWindow.closed) {
        paymentWindow.location.replace(safeUrl);
      } else {
        setCheckoutUrl(safeUrl);
      }
      setPaymentId(checkout.payment.id);
      setState('pending');
    } catch {
      paymentWindow?.close();
      setState('checkout_error');
      setError('Не удалось открыть оплату. Попробуйте ещё раз — повторное нажатие не создаст дубль.');
    }
  };

  const working = state === 'checking' || state === 'creating' || state === 'pending';
  const active = state === 'active';
  const recoveryFailed = state === 'recovery_error';

  const retryRecovery = () => {
    setState('checking');
    setRecoveryAttempt((attempt) => attempt + 1);
  };

  const publish = async () => {
    if (!csrfToken || !projectId || !artifactId || revision < 1 || publicationPending) return;
    const domains = [...new Set(
      allowedDomains
        .split(/[\s,]+/)
        .map((domain) => domain.trim())
        .filter(Boolean),
    )];
    setPublicationPending(true);
    setPublicationError(null);
    try {
      const next = await publishProject(
        projectId,
        {
          artifact_id: artifactId,
          revision,
          ...(domains.length > 0 ? { allowed_domains: domains } : {}),
        },
        csrfToken,
      );
      if (publication && publication.release_id !== next.release_id) {
        setPriorReleases((current) => [
          ...current.filter(({ release_id }) => release_id !== publication.release_id),
          publication,
        ]);
      }
      setPublication(next);
      setAllowedDomains(next.allowed_domains.join('\n'));
    } catch {
      setPublicationError('Не удалось опубликовать виджет. Проверьте домены и попробуйте ещё раз.');
    } finally {
      setPublicationPending(false);
    }
  };

  const rollbackTarget = priorReleases.at(-1) ?? null;
  const rollback = async () => {
    if (!csrfToken || !publication || !rollbackTarget || publicationPending) return;
    setPublicationPending(true);
    setPublicationError(null);
    try {
      const current = publication;
      const restored = await rollbackPublication(
        publication.publication_id,
        rollbackTarget.release_id,
        csrfToken,
      );
      setPriorReleases((known) => [
        ...known.filter(({ release_id }) => release_id !== rollbackTarget.release_id),
        current,
      ]);
      setPublication(restored);
      setAllowedDomains(restored.allowed_domains.join('\n'));
    } catch {
      setPublicationError('Не удалось откатить публикацию. Попробуйте ещё раз.');
    } finally {
      setPublicationPending(false);
    }
  };

  const embedSnippet = publication
    ? `<script src="${publication.embed_url}" async></script>`
    : null;

  return (
    <aside id="studio-publication" className="studio-upgrade" aria-labelledby="studio-upgrade-title">
      {active
        ? <CheckCircle aria-hidden size={22} weight="fill" />
        : <Sparkle aria-hidden size={22} weight="fill" />}
      <div>
        <h2 id="studio-upgrade-title">
          {active ? 'Тариф активирован' : 'Бесплатный результат готов'}
        </h2>
        <p>
          {active
            ? 'Чат в предпросмотре нужен для проверки ответов посетителю. Он не изменяет сам виджет.'
            : 'Он останется доступен в проекте. Тариф открывает публикацию и подключение виджета к вашему сайту.'}
        </p>
        {working && (
          <p className="studio-upgrade__status" role="status">
            {state === 'checking'
              ? 'Проверяем тариф…'
              : state === 'creating'
                ? 'Создаём безопасную платёжную ссылку…'
                : 'Ожидаем подтверждение оплаты…'}
          </p>
        )}
        {error && <p className="studio-upgrade__error" role="alert">{error}</p>}
        {checkoutUrl && state !== 'active' && (
          <a
            className="studio-upgrade__checkout"
            href={checkoutUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            Перейти к оплате <ArrowRight aria-hidden size={18} />
          </a>
        )}
        {active && (
          <div className="studio-upgrade__publication">
            <label htmlFor="studio-publication-domains">Разрешённые домены</label>
            <textarea
              id="studio-publication-domains"
              value={allowedDomains}
              onChange={(event) => setAllowedDomains(event.target.value)}
              placeholder="https://example.com"
              disabled={publicationPending}
            />
            <p>По одному HTTPS origin в строке. Если оставить поле пустым, сервер разрешит домен исходного сайта.</p>
            {publicationError && <p className="studio-upgrade__error" role="alert">{publicationError}</p>}
            {embedSnippet && publication && (
              <>
                <p role="status">Ревизия {publication.revision} опубликована. Stable embed URL не меняется при обновлениях.</p>
                <code>{embedSnippet}</code>
              </>
            )}
          </div>
        )}
      </div>
      {active ? (
        <div className="studio-upgrade__actions">
          <button type="button" onClick={() => void publish()} disabled={publicationPending}>
            {publicationPending ? <Clock aria-hidden size={18} /> : <ArrowRight aria-hidden size={18} />}
            {publication ? 'Обновить публикацию' : 'Опубликовать виджет'}
          </button>
          {rollbackTarget && (
            <button type="button" onClick={() => void rollback()} disabled={publicationPending}>
              Откатить к ревизии {rollbackTarget.revision}
            </button>
          )}
        </div>
      ) : recoveryFailed ? (
        <button type="button" onClick={retryRecovery}>
          <ArrowRight aria-hidden size={18} /> Повторить проверку
        </button>
      ) : (
        <button
          type="button"
          onClick={() => void startCheckout()}
          disabled={!csrfToken || working}
          aria-describedby="studio-upgrade-title"
        >
          {working ? <Clock aria-hidden size={18} /> : <ArrowRight aria-hidden size={18} />}
          {state === 'checking' ? 'Проверяем тариф' : working ? 'Проверяем оплату' : 'Опубликовать и подключить'}
        </button>
      )}
    </aside>
  );
}
