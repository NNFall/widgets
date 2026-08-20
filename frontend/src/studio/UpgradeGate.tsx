import { ArrowRight, CheckCircle, Clock, Copy, Sparkle } from '@phosphor-icons/react';
import { useCallback, useEffect, useRef, useState } from 'react';

import {
  BuilderApiError,
  claimFounderAccess,
  createBillingCheckout,
  createCustomerContact,
  disableBillingAutoRenew,
  getBillingOffer,
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
import { PublicationAccessView } from './PublicationAccessView';
import { SupportDialog } from './SupportDialog';
import type { BillingOffer, BillingSubscription, SaasProjectVersion } from './types';

const DEFAULT_POLL_INTERVAL_MS = 3_000;
const DEFAULT_MAX_POLL_ATTEMPTS = 400;
const EMPTY_OFFER: BillingOffer = {
  founder: {
    eligible: false,
    reason: null,
    remaining: 0,
    capacity: 20,
    period_days: 14,
    generation_tokens: 1_500_000,
  },
  plans: [],
};

type UpgradeState =
  | 'checking'
  | 'idle'
  | 'creating'
  | 'pending'
  | 'active'
  | 'checkout_error'
  | 'recovery_error';

type CopyTarget = 'code' | 'link' | null;

interface UpgradeGateProps {
  csrfToken: string | null;
  projectId?: string;
  sourceUrl?: string;
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

function createIdempotencyKey() {
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

function rubles(amountMinor: number) {
  return `${new Intl.NumberFormat('ru-RU').format(amountMinor / 100)} ₽`;
}

function publicationFailureMessage(error: unknown) {
  const fallback = 'Не удалось опубликовать виджет. Попробуйте ещё раз.';
  if (!(error instanceof BuilderApiError)) return fallback;

  if (error.code === 'upgrade_required') {
    return 'Для публикации нужен активный тариф. Обновите статус тарифа и повторите действие.';
  }
  if (error.code === 'verified_oauth_required') {
    return 'Для публикации войдите через подтверждённый аккаунт и повторите действие.';
  }
  if (error.code !== 'publication_invalid') return fallback;

  const reason = error.raw.toLowerCase();
  if (
    reason.includes('source url')
    || reason.includes('allowed domain')
    || reason.includes('origin')
  ) {
    return 'Не удалось определить адрес сайта проекта. Проверьте, что в проекте указана публичная HTTPS-ссылка.';
  }
  if (
    reason.includes('artifact')
    || reason.includes('active run')
    || reason.includes('publication validation')
  ) {
    return 'Эта версия виджета пока не готова к публикации. Выберите проверенную версию или пересоздайте виджет.';
  }
  return 'Эта версия или адрес сайта не прошли проверку публикации. Обновите данные проекта и повторите действие.';
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
  const [autoRenewPending, setAutoRenewPending] = useState(false);
  const [autoRenewError, setAutoRenewError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [recoveryAttempt, setRecoveryAttempt] = useState(0);
  const [publication, setPublication] = useState<PublicationRelease | null>(null);
  const [priorReleases, setPriorReleases] = useState<RollbackRelease[]>([]);
  const [publicationPending, setPublicationPending] = useState(false);
  const [publicationError, setPublicationError] = useState<string | null>(null);
  const [copiedTarget, setCopiedTarget] = useState<CopyTarget>(null);
  const [copyError, setCopyError] = useState(false);
  const [offerOpen, setOfferOpen] = useState(false);
  const [offer, setOffer] = useState<BillingOffer | null>(null);
  const [offerLoading, setOfferLoading] = useState(false);
  const [offerError, setOfferError] = useState<string | null>(null);
  const [supportOpen, setSupportOpen] = useState(false);
  const [supportPending, setSupportPending] = useState(false);
  const [supportError, setSupportError] = useState<string | null>(null);
  const [supportSent, setSupportSent] = useState(false);
  const [publishAfterActivation, setPublishAfterActivation] = useState(false);
  const [publicationRestored, setPublicationRestored] = useState(false);
  const idempotencyKeyRef = useRef<string | null>(null);
  const autoRenewIntentRef = useRef<boolean | null>(null);
  const planCodeIntentRef = useRef<string | null>(null);
  const contactIdempotencyKeyRef = useRef<string | null>(null);
  const contactIntentRef = useRef<string | null>(null);

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
          planCodeIntentRef.current = null;
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
    setPublicationRestored(false);
    setPublicationError(null);
    void getProjectPublication(projectId, abort.signal)
      .then(({ publication: restored }) => {
        if (abort.signal.aborted) return;
        applyPublicationState(restored);
        setPublicationRestored(true);
        setPublicationPending(false);
      })
      .catch(() => {
        if (!abort.signal.aborted) {
          setPublicationError('Не удалось восстановить историю публикации. Обновите страницу перед публикацией.');
          setPublicationPending(false);
        }
      });
    return () => abort.abort();
  }, [applyPublicationState, projectId, state]);

  const startCheckout = async (planCode: string, autoRenew: boolean) => {
    if (!csrfToken || state === 'creating' || state === 'pending' || state === 'active') return;
    const paymentWindow = window.open('about:blank', '_blank');
    if (paymentWindow) paymentWindow.opener = null;
    setState('creating');
    setError(null);
    setCheckoutUrl(null);
    const idempotencyKey = idempotencyKeyRef.current ?? createIdempotencyKey();
    const requestedPlanCode = planCodeIntentRef.current ?? planCode;
    const autoRenewIntent = autoRenewIntentRef.current ?? autoRenew;
    idempotencyKeyRef.current = idempotencyKey;
    autoRenewIntentRef.current = autoRenewIntent;
    planCodeIntentRef.current = requestedPlanCode;

    try {
      const checkout = await createBillingCheckout(
        requestedPlanCode,
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
      setOfferOpen(false);
    } catch (caught) {
      paymentWindow?.close();
      if (
        caught instanceof BuilderApiError
        && caught.code === 'intro_offer_unavailable'
      ) {
        idempotencyKeyRef.current = null;
        autoRenewIntentRef.current = null;
        planCodeIntentRef.current = null;
        setState('idle');
        setOffer((currentOffer) => currentOffer
          ? {
            ...currentOffer,
            plans: currentOffer.plans.filter(({ code }) => code !== 'starter_intro_15d'),
          }
          : currentOffer);
        setOfferError('Вводный тариф уже использован. Выберите обычный тариф.');
        setOfferOpen(true);
        return;
      }
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

  const openOffer = async () => {
    if (!csrfToken || !projectId || working) return;
    if (
      state === 'checkout_error'
      && idempotencyKeyRef.current
      && planCodeIntentRef.current
    ) {
      await startCheckout(
        planCodeIntentRef.current,
        autoRenewIntentRef.current ?? false,
      );
      return;
    }
    setOfferOpen(true);
    setOfferError(null);
    if (offer) return;
    setOfferLoading(true);
    try {
      setOffer(await getBillingOffer(projectId));
    } catch {
      setOfferError('Не удалось загрузить условия публикации. Попробуйте ещё раз.');
    } finally {
      setOfferLoading(false);
    }
  };

  const claimFounder = async () => {
    if (!csrfToken || !projectId || working) return;
    setState('creating');
    setOfferError(null);
    try {
      const result = await claimFounderAccess(projectId, csrfToken);
      setSubscription(result.subscription);
      setState('active');
      setOfferOpen(false);
      setPublishAfterActivation(true);
    } catch {
      setState('idle');
      setOfferError('Founder-доступ уже занят или сейчас недоступен. Выберите тариф или повторите позже.');
    }
  };

  const sendSupport = async (input: {
    message: string;
    rating?: number;
    testimonialAllowed: boolean;
  }) => {
    if (!csrfToken || !projectId || supportPending) return;
    const kind = subscription?.access_kind === 'founder' ? 'founder_feedback' : 'support';
    const normalizedMessage = input.message.trim();
    const intent = JSON.stringify({
      projectId,
      kind,
      message: normalizedMessage,
      rating: input.rating ?? null,
      testimonialAllowed: input.testimonialAllowed,
    });
    const idempotencyKey = (
      contactIntentRef.current === intent && contactIdempotencyKeyRef.current
    ) || createIdempotencyKey();
    contactIntentRef.current = intent;
    contactIdempotencyKeyRef.current = idempotencyKey;
    setSupportPending(true);
    setSupportError(null);
    try {
      await createCustomerContact(projectId, normalizedMessage, csrfToken, idempotencyKey, {
        kind,
        rating: input.rating,
        testimonialAllowed: input.testimonialAllowed,
      });
      contactIdempotencyKeyRef.current = null;
      contactIntentRef.current = null;
      setSupportSent(true);
    } catch {
      setSupportError('Не удалось отправить сообщение. Текст сохранён в форме — попробуйте ещё раз.');
    } finally {
      setSupportPending(false);
    }
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

  const publish = useCallback(async () => {
    const targetAvailable = versionsEnabled
      ? Boolean(projectVersionId)
      : Boolean(artifactId) && revision >= 1;
    if (
      !csrfToken
      || !projectId
      || !targetAvailable
      || publicationPending
      || !publicationRestored
    ) return;
    const domainPayload = publication
      ? { allowed_domains: publication.allowed_domains }
      : {};
    setPublicationPending(true);
    setPublicationError(null);
    try {
      const next = await publishProject(
        projectId,
        versionsEnabled
          ? {
              project_version_id: projectVersionId,
              expected_active_release_id: publication?.release_id ?? null,
              ...domainPayload,
            }
          : {
              artifact_id: artifactId,
              revision,
              ...domainPayload,
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
        setPublicationError(publicationFailureMessage(caught));
      }
    } finally {
      setPublicationPending(false);
    }
  }, [
    artifactId,
    csrfToken,
    projectId,
    projectVersionId,
    publication,
    publicationPending,
    publicationRestored,
    reloadPublicationAfterConflict,
    revision,
    versionsEnabled,
  ]);

  useEffect(() => {
    if (
      !publishAfterActivation
      || state !== 'active'
      || !publicationRestored
      || publicationPending
    ) return;
    setPublishAfterActivation(false);
    void publish();
  }, [
    publicationPending,
    publicationRestored,
    publish,
    publishAfterActivation,
    state,
  ]);

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
      ? 'Виджет опубликован. Скопируйте код установки или постоянную ссылку ниже.'
      : 'Ничего настраивать не нужно: сайт проекта будет разрешён автоматически. После публикации появится готовый код.'
    : 'Первая версия сохранена. Выберите бесплатный founder-пилот или подходящий тариф — условия будут показаны до перехода к оплате.';
  const launchSteps = [
    {
      title: 'Доступ',
      copy: active ? 'Подключён и готов к работе' : 'Выберите Founder-пилот или платный тариф',
      state: active ? 'completed' : 'current',
    },
    {
      title: 'Публикация',
      copy: publication ? 'Выбранная версия уже доступна' : 'Вы сами выбираете момент запуска',
      state: publication ? 'completed' : active ? 'current' : 'upcoming',
    },
    {
      title: 'Установка',
      copy: publication ? 'Осталось добавить код на сайт' : 'Код появится после публикации',
      state: publication ? 'current' : 'upcoming',
    },
  ] as const;

  const copyText = async (value: string, target: Exclude<CopyTarget, null>) => {
    setCopiedTarget(null);
    setCopyError(false);
    if (!navigator.clipboard?.writeText) {
      setCopyError(true);
      return;
    }
    try {
      await navigator.clipboard.writeText(value);
      setCopiedTarget(target);
    } catch {
      setCopyError(true);
    }
  };

  return (
    <>
    <aside id="studio-publication" className="studio-upgrade" aria-labelledby="studio-upgrade-title">
      {active
        ? <CheckCircle aria-hidden size={22} weight="fill" />
        : <Sparkle aria-hidden size={22} weight="fill" />}
      <div>
        <h2 id="studio-upgrade-title">{heading}</h2>
        <p>{description}</p>
        <ol className="studio-upgrade__launch-steps" aria-label="Путь до запуска виджета">
          {launchSteps.map((step, index) => (
            <li key={step.title} data-state={step.state} aria-current={step.state === 'current' ? 'step' : undefined}>
              <span aria-hidden>{step.state === 'completed' ? <CheckCircle size={16} weight="fill" /> : index + 1}</span>
              <div>
                <strong>{step.title}</strong>
                <small>{step.copy}</small>
              </div>
            </li>
          ))}
        </ol>
        {!active && (
          <p className="studio-upgrade__safety-note">
            Founder-пилот не требует карты. Для платного варианта ЮKassa откроется в защищённом окне, а автопродление включится только после отдельного согласия.
          </p>
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
              {subscription?.next_charge
                ? `Следующее списание — ${rubles(subscription.next_charge.amount_minor)} ${subscriptionEndLabel(subscription.next_charge.at)}. Автопродление включено.`
                : subscription?.auto_renew
                  ? `Следующее продление — ${subscriptionEndLabel(subscription.next_renewal_at ?? subscription.current_period_end)}. Автопродление включено.`
                : `Тариф действует до ${subscriptionEndLabel(subscription?.current_period_end)}. Автопродление выключено.`}
            </p>
            {typeof subscription?.generation_tokens_remaining === 'number' && (
              <p className="studio-upgrade__renewal-status">
                {subscription.generation_tokens_remaining > 0
                  ? 'Доработки доступны в рамках тарифа.'
                  : 'Лимит доработок на тарифе исчерпан.'}
              </p>
            )}
            {publicationError && <p className="studio-upgrade__error" role="alert">{publicationError}</p>}
            {embedSnippet && publication && (
              <>
                <p role="status">
                  {versionsEnabled && publishedVersionOrdinal
                    ? `Версия ${publishedVersionOrdinal} опубликована и доступна на разрешённых сайтах.`
                    : 'Виджет опубликован и доступен на разрешённых сайтах.'}
                </p>
                <section className="studio-upgrade__handoff" aria-labelledby="studio-upgrade-handoff-title">
                  <span>Остался один шаг</span>
                  <h3 id="studio-upgrade-handoff-title">Установите виджет на сайт</h3>
                  <p>Скопируйте код установки или передайте код человеку, который управляет сайтом. Последующие обновления будут приходить по тому же адресу.</p>
                  <div className="studio-upgrade__handoff-actions">
                    <button type="button" onClick={() => void copyText(embedSnippet, 'code')}>
                      <Copy aria-hidden size={18} weight="bold" /> Скопировать код установки
                    </button>
                    <button type="button" onClick={() => void copyText(publication.embed_url, 'link')}>
                      <Copy aria-hidden size={18} weight="bold" /> Скопировать ссылку загрузчика
                    </button>
                  </div>
                  <a className="studio-upgrade__install-guide" href="/install">
                    Открыть инструкцию по установке
                  </a>
                  {copiedTarget === 'code' && <p className="studio-upgrade__copy-status" role="status">Код скопирован. Его можно отправить разработчику.</p>}
                  {copiedTarget === 'link' && <p className="studio-upgrade__copy-status" role="status">Ссылка загрузчика скопирована.</p>}
                  {copyError && (
                    <div className="studio-upgrade__manual-copy">
                      <p className="studio-upgrade__copy-status studio-upgrade__copy-status--error" role="alert">
                        Не получилось скопировать автоматически. Скопируйте вручную оба значения ниже.
                      </p>
                      <div>
                        <strong>Код установки</strong>
                        <code tabIndex={0}>{embedSnippet}</code>
                      </div>
                      <div>
                        <strong>Ссылка загрузчика</strong>
                        <code tabIndex={0}>{publication.embed_url}</code>
                      </div>
                    </div>
                  )}
                </section>
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
          <button
            type="button"
            onClick={() => void publish()}
            disabled={publicationPending || !publicationRestored}
          >
            {publicationPending ? <Clock aria-hidden size={18} /> : <ArrowRight aria-hidden size={18} />}
            {publication ? 'Обновить публикацию' : 'Опубликовать и получить код'}
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
          <button type="button" onClick={() => {
            setSupportError(null);
            setSupportSent(false);
            setSupportOpen(true);
          }}>
            Связаться с Kaigo
          </button>
        </div>
      ) : recoveryFailed ? (
        <button type="button" onClick={retryRecovery}>
          <ArrowRight aria-hidden size={18} /> Повторить проверку
        </button>
      ) : (
        <button
          type="button"
          onClick={() => void openOffer()}
          disabled={!csrfToken || working}
          aria-describedby="studio-upgrade-title"
        >
          {working ? <Clock aria-hidden size={18} /> : <ArrowRight aria-hidden size={18} />}
          {state === 'checking' ? 'Проверяем доступ' : working ? 'Проверяем оплату' : 'Выбрать условия публикации'}
        </button>
      )}
    </aside>
    {offerOpen && (
      <PublicationAccessView
        offer={offer ?? EMPTY_OFFER}
        loading={offerLoading}
        busy={working}
        error={offerError}
        onFounder={() => void claimFounder()}
        onCheckout={(planCode, autoRenew) => void startCheckout(planCode, autoRenew)}
      />
    )}
    <SupportDialog
      open={supportOpen}
      founder={subscription?.access_kind === 'founder'}
      busy={supportPending}
      error={supportError}
      sent={supportSent}
      onClose={() => setSupportOpen(false)}
      onSubmit={(input) => void sendSupport(input)}
    />
    </>
  );
}
