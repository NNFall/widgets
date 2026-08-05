import { ArrowRight, CheckCircle, Clock, Sparkle } from '@phosphor-icons/react';
import { useCallback, useEffect, useRef, useState } from 'react';

import {
  BuilderApiError,
  createBillingCheckout,
  disableBillingAutoRenew,
  getBillingPayment,
  getPendingBillingPayment,
  getBillingSubscription,
  getProjectPublication,
  publishProject,
  rollbackLegacyPublication,
  rollbackPublication,
  resumeBillingPayment,
  type PublicationRelease,
  type ProjectPublicationState,
} from './api';
import type { BillingSubscription, SaasProjectVersion } from './types';

const DEFAULT_PLAN_CODE = 'starter_monthly';
const DEFAULT_POLL_INTERVAL_MS = 3_000;
const DEFAULT_MAX_POLL_ATTEMPTS = 400;

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
  versionsEnabled?: boolean;
  projectVersionId?: string;
  projectVersionOrdinal?: number;
  projectVersions?: Array<Pick<SaasProjectVersion, 'id' | 'ordinal'>>;
  artifactId?: string;
  revision?: number;
  pollIntervalMs?: number;
  maxPollAttempts?: number;
}

type RollbackRelease = Pick<PublicationRelease, 'release_id' | 'revision' | 'project_version_id'>;

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

function subscriptionEndLabel(value: string | undefined) {
  if (!value) return 'конца оплаченного периода';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'конца оплаченного периода';
  return new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  }).format(date).replace(/\.$/, '');
}

export function UpgradeGate({
  csrfToken,
  projectId = '',
  versionsEnabled = false,
  projectVersionId = '',
  projectVersionOrdinal,
  projectVersions = [],
  artifactId = '',
  revision = 0,
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
  maxPollAttempts = DEFAULT_MAX_POLL_ATTEMPTS,
}: UpgradeGateProps) {
  const [state, setState] = useState<UpgradeState>(csrfToken ? 'checking' : 'idle');
  const [paymentId, setPaymentId] = useState<string | null>(null);
  const [checkoutUrl, setCheckoutUrl] = useState<string | null>(null);
  const [subscription, setSubscription] = useState<BillingSubscription | null>(null);
  const [autoRenewConsent, setAutoRenewConsent] = useState(false);
  const [autoRenewPending, setAutoRenewPending] = useState(false);
  const [autoRenewError, setAutoRenewError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [recoveryAttempt, setRecoveryAttempt] = useState(0);
  const [allowedDomains, setAllowedDomains] = useState('');
  const [publication, setPublication] = useState<PublicationRelease | null>(null);
  const [priorReleases, setPriorReleases] = useState<RollbackRelease[]>([]);
  const [publicationPending, setPublicationPending] = useState(false);
  const [publicationError, setPublicationError] = useState<string | null>(null);
  const idempotencyKeyRef = useRef<string | null>(null);
  const autoRenewIntentRef = useRef<boolean | null>(null);

  const applyPublicationState = useCallback((restored: ProjectPublicationState | null) => {
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
      project_version_id: activeRelease.project_version_id ?? null,
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
  }, []);

  const reloadPublicationAfterConflict = useCallback(async () => {
    if (!projectId) return false;
    try {
      const { publication: restored } = await getProjectPublication(projectId);
      applyPublicationState(restored);
      return true;
    } catch {
      return false;
    }
  }, [applyPublicationState, projectId]);

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
          setSubscription(subscription);
          setState('active');
          return;
        }
        setSubscription(null);
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
          autoRenewIntentRef.current = null;
          setState('checkout_error');
          setError('Оплата не завершена. Можно открыть платёжную страницу и попробовать ещё раз.');
          return;
        }
        if (payment.status === 'succeeded') {
          const { subscription } = await getBillingSubscription(abort.signal);
          if (abort.signal.aborted) return;
          if (subscription?.status === 'active') {
            setSubscription(subscription);
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
        applyPublicationState(restored);
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
  }, [applyPublicationState, projectId, state]);

  const startCheckout = async () => {
    if (!csrfToken || state === 'creating' || state === 'pending' || state === 'active') return;
    const paymentWindow = window.open('about:blank', '_blank');
    if (paymentWindow) paymentWindow.opener = null;
    setState('creating');
    setError(null);
    setCheckoutUrl(null);
    const idempotencyKey = idempotencyKeyRef.current ?? checkoutKey();
    const autoRenewIntent = autoRenewIntentRef.current ?? autoRenewConsent;
    idempotencyKeyRef.current = idempotencyKey;
    autoRenewIntentRef.current = autoRenewIntent;

    try {
      const checkout = await createBillingCheckout(
        DEFAULT_PLAN_CODE,
        csrfToken,
        idempotencyKey,
        projectId,
        autoRenewIntent,
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

  const disableAutoRenew = async () => {
    if (!csrfToken || !subscription?.auto_renew || autoRenewPending) return;
    setAutoRenewPending(true);
    setAutoRenewError(null);
    try {
      const result = await disableBillingAutoRenew(subscription.id, csrfToken);
      setSubscription(result.subscription);
    } catch {
      setAutoRenewError('Не удалось отключить автопродление. Попробуйте ещё раз.');
    } finally {
      setAutoRenewPending(false);
    }
  };

  const publish = async () => {
    const targetAvailable = versionsEnabled
      ? Boolean(projectVersionId)
      : Boolean(artifactId) && revision >= 1;
    if (!csrfToken || !projectId || !targetAvailable || publicationPending) return;
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
        versionsEnabled
          ? {
              project_version_id: projectVersionId,
              expected_active_release_id: publication?.release_id ?? null,
              ...(domains.length > 0 ? { allowed_domains: domains } : {}),
            }
          : {
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
    } catch (caught) {
      if (
        caught instanceof BuilderApiError
        && caught.status === 409
        && caught.code === 'publication_conflict'
      ) {
        const reloaded = await reloadPublicationAfterConflict();
        setPublicationError(reloaded
          ? 'Публикация изменилась в другой сессии. Данные обновлены — проверьте их и повторите действие.'
          : 'Публикация изменилась в другой сессии, но не удалось обновить её состояние. Повторите проверку.');
      } else {
        setPublicationError('Не удалось опубликовать виджет. Проверьте домены и попробуйте ещё раз.');
      }
    } finally {
      setPublicationPending(false);
    }
  };

  const rollbackTarget = priorReleases.at(-1) ?? null;
  const versionOrdinal = (versionId: string | null | undefined) => {
    if (!versionId) return undefined;
    return projectVersions.find(({ id }) => id === versionId)?.ordinal
      ?? (versionId === projectVersionId ? projectVersionOrdinal : undefined);
  };
  const publishedVersionOrdinal = versionOrdinal(publication?.project_version_id);
  const rollbackVersionOrdinal = versionOrdinal(rollbackTarget?.project_version_id);
  const rollback = async () => {
    if (!csrfToken || !publication || !rollbackTarget || publicationPending) return;
    setPublicationPending(true);
    setPublicationError(null);
    try {
      const current = publication;
      const restored = versionsEnabled
        ? await rollbackPublication(
            publication.publication_id,
            rollbackTarget.release_id,
            publication.release_id,
            csrfToken,
          )
        : await rollbackLegacyPublication(
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
    } catch (caught) {
      if (
        caught instanceof BuilderApiError
        && caught.status === 409
        && caught.code === 'publication_conflict'
      ) {
        const reloaded = await reloadPublicationAfterConflict();
        setPublicationError(reloaded
          ? 'Публикация изменилась в другой сессии. Данные обновлены — проверьте их и повторите действие.'
          : 'Публикация изменилась в другой сессии. Обновите страницу перед повтором.');
      } else {
        setPublicationError('Не удалось откатить публикацию. Попробуйте ещё раз.');
      }
    } finally {
      setPublicationPending(false);
    }
  };

  const embedSnippet = publication
    ? `<script src="${publication.embed_url}" async></script>`
    : null;
  const heading = active
    ? publication ? 'Виджет опубликован' : 'Всё готово к публикации'
    : 'Подключите виджет к сайту';
  const description = active
    ? publication
      ? 'Виджет уже доступен на разрешённых сайтах. Новую версию можно опубликовать здесь же.'
      : 'Укажите сайты, проверьте виджет и опубликуйте его. Код подключения появится после публикации.'
    : 'Первая версия сохранена. Тариф открывает публикацию, доработки и подключение виджета к сайту.';

  return (
    <aside id="studio-publication" className="studio-upgrade" aria-labelledby="studio-upgrade-title">
      {active
        ? <CheckCircle aria-hidden size={22} weight="fill" />
        : <Sparkle aria-hidden size={22} weight="fill" />}
      <div>
        <h2 id="studio-upgrade-title">{heading}</h2>
        <p>{description}</p>
        {!active && (
          <label className="studio-upgrade__renewal-consent">
            <input
              type="checkbox"
              checked={autoRenewConsent}
              onChange={(event) => setAutoRenewConsent(event.target.checked)}
              disabled={working || idempotencyKeyRef.current !== null}
            />
            <span>
              <strong>Продлевать тариф автоматически</strong>
              <small>ЮKassa сохранит способ оплаты только после успешного платежа. Автопродление можно отключить в любой момент.</small>
            </span>
          </label>
        )}
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
        {autoRenewError && (
          <p className="studio-upgrade__error" role="alert">{autoRenewError}</p>
        )}
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
            <p className="studio-upgrade__renewal-status">
              {subscription?.auto_renew
                ? `Следующее продление — ${subscriptionEndLabel(subscription.next_renewal_at ?? subscription.current_period_end)}. Автопродление включено.`
                : `Тариф действует до ${subscriptionEndLabel(subscription?.current_period_end)}. Автопродление выключено.`}
            </p>
            {typeof subscription?.generation_tokens_remaining === 'number' && (
              <p className="studio-upgrade__renewal-status">
                Доступный объём доработок: {new Intl.NumberFormat('ru-RU').format(
                  Math.max(0, subscription.generation_tokens_remaining),
                )} токенов
              </p>
            )}
            <label htmlFor="studio-publication-domains">На каких сайтах разрешить виджет</label>
            <textarea
              id="studio-publication-domains"
              value={allowedDomains}
              onChange={(event) => setAllowedDomains(event.target.value)}
              placeholder="https://example.com"
              disabled={publicationPending}
            />
            <p>Укажите каждый адрес с новой строки, например https://example.com. Если поле пустое, будет использован сайт проекта.</p>
            {publicationError && <p className="studio-upgrade__error" role="alert">{publicationError}</p>}
            {embedSnippet && publication && (
              <>
                <p role="status">
                  {versionsEnabled && publishedVersionOrdinal
                    ? `Версия ${publishedVersionOrdinal} опубликована и доступна на разрешённых сайтах.`
                    : 'Виджет опубликован и доступен на разрешённых сайтах.'}
                </p>
                <details className="studio-upgrade__developer">
                  <summary>Код для разработчика</summary>
                  <div>
                    <p>Передайте этот код человеку, который управляет сайтом.</p>
                    <code>{embedSnippet}</code>
                    <dl>
                      <div>
                        <dt>Постоянный адрес подключения</dt>
                        <dd>{publication.embed_url}</dd>
                      </div>
                      <div>
                        <dt>Версия файла</dt>
                        <dd>{publication.revision}</dd>
                      </div>
                      <div>
                        <dt>Публикация</dt>
                        <dd>{publication.release_id}</dd>
                      </div>
                      <div>
                        <dt>Сборка</dt>
                        <dd>{publication.artifact_id}</dd>
                      </div>
                    </dl>
                  </div>
                </details>
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
              {versionsEnabled && rollbackVersionOrdinal
                ? `Вернуть версию ${rollbackVersionOrdinal}`
                : 'Вернуть предыдущую публикацию'}
            </button>
          )}
          {subscription?.auto_renew && (
            <button
              type="button"
              onClick={() => void disableAutoRenew()}
              disabled={autoRenewPending}
            >
              {autoRenewPending ? <Clock aria-hidden size={18} /> : null}
              Отключить автопродление
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
