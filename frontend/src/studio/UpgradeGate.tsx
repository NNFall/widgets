import { ArrowRight, CheckCircle, Clock, Sparkle } from '@phosphor-icons/react';
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
import { PublicationInstallView } from './PublicationInstallView';
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

type PublicationPhase =
  | 'checking'
  | 'access'
  | 'activating'
  | 'publishing'
  | 'publish_error'
  | 'install';

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
  const [publicationPhase, setPublicationPhase] = useState<PublicationPhase>('checking');
  const [offer, setOffer] = useState<BillingOffer | null>(null);
  const [offerLoading, setOfferLoading] = useState(false);
  const [offerError, setOfferError] = useState<string | null>(null);
  const [offerAttempt, setOfferAttempt] = useState(0);
  const [supportOpen, setSupportOpen] = useState(false);
  const [supportPending, setSupportPending] = useState(false);
  const [supportError, setSupportError] = useState<string | null>(null);
  const [supportSent, setSupportSent] = useState(false);
  const [publicationRestored, setPublicationRestored] = useState(false);
  const [founderFeedbackProjectId, setFounderFeedbackProjectId] = useState<string | null>(null);
  const idempotencyKeyRef = useRef<string | null>(null);
  const autoRenewIntentRef = useRef<boolean | null>(null);
  const planCodeIntentRef = useRef<string | null>(null);
  const contactIdempotencyKeyRef = useRef<string | null>(null);
  const contactIntentRef = useRef<string | null>(null);
  const activeProjectIdRef = useRef(projectId);
  const mountedRef = useRef(true);
  const previousProjectIdRef = useRef(projectId);
  const projectGenerationRef = useRef(0);
  const offerRequestRef = useRef<string | null>(null);
  const publicationRecoveryRef = useRef<{
    projectId: string;
    resolved: boolean;
    promise: Promise<PublicationRelease | null>;
  } | null>(null);
  const publishInFlightRef = useRef<Promise<boolean> | null>(null);
  const firstPublishAttemptedRef = useRef(new Set<string>());
  const billingValidatedProjectRef = useRef<string | null>(null);
  const copyRequestSequenceRef = useRef(0);

  if (previousProjectIdRef.current !== projectId) {
    previousProjectIdRef.current = projectId;
    projectGenerationRef.current += 1;
  }
  activeProjectIdRef.current = projectId;

  const isCurrentProject = useCallback((
    expectedProjectId: string | null,
    expectedGeneration: number,
  ) => (
    mountedRef.current
    && activeProjectIdRef.current === expectedProjectId
    && projectGenerationRef.current === expectedGeneration
  ), []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const applyPublicationState = useCallback((restored: ProjectPublicationState | null) => {
    if (restored === null) {
      setPublication(null);
      setPriorReleases([]);
      return null;
    }
    const activeRelease = restored.active_release;
    const nextPublication: PublicationRelease = {
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
    };
    setPublication(nextPublication);
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
    return nextPublication;
  }, []);

  const reloadPublicationAfterConflict = useCallback(async (expectedGeneration: number) => {
    if (!projectId) return undefined;
    try {
      const { publication: restored } = await getProjectPublication(projectId);
      if (!isCurrentProject(projectId, expectedGeneration)) return undefined;
      const nextPublication = applyPublicationState(restored);
      publicationRecoveryRef.current = {
        projectId,
        resolved: true,
        promise: Promise.resolve(nextPublication),
      };
      setPublicationRestored(true);
      return nextPublication;
    } catch {
      if (isCurrentProject(projectId, expectedGeneration)) {
        publicationRecoveryRef.current = null;
        setPublicationRestored(false);
      }
      return undefined;
    }
  }, [applyPublicationState, isCurrentProject, projectId]);

  useEffect(() => {
    billingValidatedProjectRef.current = null;
    if (projectId) firstPublishAttemptedRef.current.delete(projectId);
    publicationRecoveryRef.current = null;
    publishInFlightRef.current = null;
    offerRequestRef.current = null;
    idempotencyKeyRef.current = null;
    autoRenewIntentRef.current = null;
    planCodeIntentRef.current = null;
    setPublication(null);
    setPriorReleases([]);
    setPublicationRestored(false);
    setPublicationPending(false);
    setPublicationError(null);
    setCopiedTarget(null);
    setCopyError(false);
    setOffer(null);
    setOfferError(null);
    setSupportOpen(false);
    setSupportPending(false);
    setSupportError(null);
    setSupportSent(false);
    contactIdempotencyKeyRef.current = null;
    contactIntentRef.current = null;
    copyRequestSequenceRef.current += 1;
    setFounderFeedbackProjectId(null);
    setPublicationPhase('checking');
  }, [projectId]);

  const ensurePublicationRecovered = useCallback((): Promise<PublicationRelease | null> => {
    if (!projectId) return Promise.reject(new Error('publication_project_required'));
    if (publicationRecoveryRef.current?.projectId === projectId) {
      return publicationRecoveryRef.current.promise;
    }

    setPublicationPending(true);
    setPublicationRestored(false);
    setPublicationError(null);
    const requestGeneration = projectGenerationRef.current;
    const recovery = getProjectPublication(projectId)
      .then(({ publication: restored }) => {
        if (!isCurrentProject(projectId, requestGeneration)) {
          throw new Error('stale_publication_recovery');
        }
        const nextPublication = applyPublicationState(restored);
        if (publicationRecoveryRef.current?.projectId === projectId) {
          publicationRecoveryRef.current.resolved = true;
        }
        setPublicationRestored(true);
        return nextPublication;
      })
      .catch((caught) => {
        if (isCurrentProject(projectId, requestGeneration)) {
          setPublicationRestored(false);
          setPublicationError('Не удалось восстановить историю публикации. Обновите страницу перед публикацией.');
          setPublicationPhase('publish_error');
        }
        throw caught;
      })
      .finally(() => {
        if (isCurrentProject(projectId, requestGeneration)) {
          setPublicationPending(false);
        }
      });
    publicationRecoveryRef.current = { projectId, resolved: false, promise: recovery };
    return recovery;
  }, [applyPublicationState, isCurrentProject, projectId]);

  useEffect(() => {
    billingValidatedProjectRef.current = null;
    if (!csrfToken) {
      setState('idle');
      return undefined;
    }
    const abort = new AbortController();
    setState('checking');
    setPublicationPhase('checking');
    setError(null);
    void (async () => {
      try {
        const { subscription } = await getBillingSubscription(abort.signal);
        if (abort.signal.aborted) return;
        if (subscription?.status === 'active') {
          setSubscription(subscription);
          billingValidatedProjectRef.current = projectId;
          setState('active');
          setPublicationPhase('checking');
          return;
        }
        setSubscription(null);
        const pending = await getPendingBillingPayment(abort.signal);
        if (abort.signal.aborted) return;
        if (pending.payment === null && pending.checkout_url === null) {
          setPaymentId(null);
          setCheckoutUrl(null);
          setState('idle');
          setPublicationPhase('access');
          if (projectId) {
            const requestKey = `${projectId}:${offerAttempt}`;
            if (offerRequestRef.current !== requestKey) {
              offerRequestRef.current = requestKey;
              setOfferLoading(true);
              setOfferError(null);
              try {
                const nextOffer = await getBillingOffer(projectId);
                if (!abort.signal.aborted && activeProjectIdRef.current === projectId) {
                  setOffer(nextOffer);
                }
              } catch {
                if (!abort.signal.aborted && activeProjectIdRef.current === projectId) {
                  setOfferError('Не удалось загрузить условия публикации. Попробуйте ещё раз.');
                }
              } finally {
                if (!abort.signal.aborted && activeProjectIdRef.current === projectId) {
                  setOfferLoading(false);
                }
              }
            }
          }
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
        setPublicationPhase('access');
      } catch {
        if (abort.signal.aborted) return;
        setState('recovery_error');
        setPublicationPhase('checking');
        setError('Не удалось проверить тариф и незавершённую оплату. Повторите проверку.');
      }
    })();
    return () => abort.abort();
  }, [csrfToken, offerAttempt, projectId, recoveryAttempt]);

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
            billingValidatedProjectRef.current = projectId;
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
  }, [maxPollAttempts, paymentId, pollIntervalMs, projectId, state]);

  const startCheckout = async (planCode: string, autoRenew: boolean) => {
    if (!csrfToken || state === 'creating' || state === 'pending' || state === 'active') return;
    const requestGeneration = projectGenerationRef.current;
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
      if (!isCurrentProject(projectId, requestGeneration)) {
        if (idempotencyKeyRef.current === idempotencyKey) {
          idempotencyKeyRef.current = null;
          autoRenewIntentRef.current = null;
          planCodeIntentRef.current = null;
        }
        paymentWindow?.close();
        return;
      }
      const safeUrl = safeCheckoutUrl(checkout.checkout_url);
      if (!safeUrl) throw new Error('invalid_checkout_url');
      if (paymentWindow && !paymentWindow.closed) {
        paymentWindow.location.replace(safeUrl);
      } else {
        setCheckoutUrl(safeUrl);
      }
      setPaymentId(checkout.payment.id);
      setState('pending');
      setPublicationPhase('access');
    } catch (caught) {
      paymentWindow?.close();
      if (!isCurrentProject(projectId, requestGeneration)) {
        if (idempotencyKeyRef.current === idempotencyKey) {
          idempotencyKeyRef.current = null;
          autoRenewIntentRef.current = null;
          planCodeIntentRef.current = null;
        }
        return;
      }
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
        setPublicationPhase('access');
        return;
      }
      setState('checkout_error');
      setError('Не удалось открыть оплату. Попробуйте ещё раз — повторное нажатие не создаст дубль.');
    }
  };

  const working = state === 'checking' || state === 'creating' || state === 'pending';
  const active = state === 'active';
  const recoveryFailed = state === 'recovery_error';
  const founderFeedback = (
    publication !== null
    && subscription?.access_kind === 'founder'
    && founderFeedbackProjectId === projectId
  );
  const retryRecovery = () => {
    if (csrfToken && projectId && !offer) {
      const requestGeneration = projectGenerationRef.current;
      const requestKey = `${projectId}:${offerAttempt}`;
      offerRequestRef.current = requestKey;
      setOfferLoading(true);
      setOfferError(null);
      void getBillingOffer(projectId)
        .then((nextOffer) => {
          if (isCurrentProject(projectId, requestGeneration)) {
            setOffer(nextOffer);
          }
        })
        .catch(() => {
          if (isCurrentProject(projectId, requestGeneration)) {
            offerRequestRef.current = null;
            setOfferError('Не удалось загрузить условия публикации. Попробуйте ещё раз.');
          }
        })
        .finally(() => {
          if (isCurrentProject(projectId, requestGeneration)) {
            setOfferLoading(false);
          }
        });
    }
    setState('checking');
    setPublicationPhase('checking');
    setRecoveryAttempt((attempt) => attempt + 1);
  };

  useEffect(() => {
    if (
      !csrfToken
      || !projectId
      || state === 'checking'
      || state === 'active'
      || offer
    ) return;
    const requestKey = `${projectId}:${offerAttempt}`;
    if (offerRequestRef.current === requestKey) return;
    offerRequestRef.current = requestKey;
    setPublicationPhase('access');
    setOfferLoading(true);
    setOfferError(null);
    const requestGeneration = projectGenerationRef.current;
    void getBillingOffer(projectId)
      .then((nextOffer) => {
        if (!isCurrentProject(projectId, requestGeneration)) return;
        setOffer(nextOffer);
      })
      .catch(() => {
        if (!isCurrentProject(projectId, requestGeneration)) return;
        setOfferError('Не удалось загрузить условия публикации. Попробуйте ещё раз.');
      })
      .finally(() => {
        if (isCurrentProject(projectId, requestGeneration)) {
          setOfferLoading(false);
        }
      });
  }, [csrfToken, isCurrentProject, offer, offerAttempt, projectId, state]);

  const retryOffer = () => {
    offerRequestRef.current = null;
    setOfferError(null);
    setOfferAttempt((attempt) => attempt + 1);
  };

  const sendSupport = async (input: {
    message: string;
    rating?: number;
    testimonialAllowed: boolean;
  }) => {
    if (!csrfToken || !projectId || supportPending) return;
    const requestGeneration = projectGenerationRef.current;
    const kind = founderFeedback
      ? 'founder_feedback'
      : 'support';
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
      if (!isCurrentProject(projectId, requestGeneration)) return;
      contactIdempotencyKeyRef.current = null;
      contactIntentRef.current = null;
      setSupportSent(true);
    } catch {
      if (!isCurrentProject(projectId, requestGeneration)) return;
      setSupportError('Не удалось отправить сообщение. Текст сохранён в форме — попробуйте ещё раз.');
    } finally {
      if (isCurrentProject(projectId, requestGeneration)) {
        setSupportPending(false);
      }
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

  const publish = useCallback((existingPublication: PublicationRelease | null): Promise<boolean> => {
    if (publishInFlightRef.current) return publishInFlightRef.current;
    const requestGeneration = projectGenerationRef.current;
    const targetAvailable = versionsEnabled
      ? Boolean(projectVersionId)
      : Boolean(artifactId) && revision >= 1;
    if (
      !csrfToken
      || !projectId
      || !targetAvailable
      || publicationRecoveryRef.current?.projectId !== projectId
      || !publicationRecoveryRef.current.resolved
    ) return Promise.resolve(false);
    const domainPayload = existingPublication
      ? { allowed_domains: existingPublication.allowed_domains }
      : {};
    setPublicationPending(true);
    setPublicationError(null);
    setPublicationPhase('publishing');

    const inFlight = (async () => {
      try {
        const next = await publishProject(
          projectId,
          versionsEnabled
            ? {
                project_version_id: projectVersionId,
                expected_active_release_id: existingPublication?.release_id ?? null,
                ...domainPayload,
              }
            : {
                artifact_id: artifactId,
                revision,
                ...domainPayload,
              },
          csrfToken,
        );
        if (!isCurrentProject(projectId, requestGeneration)) return false;
        if (existingPublication && existingPublication.release_id !== next.release_id) {
          setPriorReleases((current) => [
            ...current.filter(({ release_id }) => release_id !== existingPublication.release_id),
            existingPublication,
          ]);
        }
        setPublication(next);
        publicationRecoveryRef.current = {
          projectId,
          resolved: true,
          promise: Promise.resolve(next),
        };
        setPublicationPhase('install');
        return true;
      } catch (caught) {
        if (!isCurrentProject(projectId, requestGeneration)) return false;
        if (
          caught instanceof BuilderApiError
          && caught.status === 409
          && caught.code === 'publication_conflict'
        ) {
          await new Promise<void>((resolve) => setTimeout(resolve, 0));
          if (!isCurrentProject(projectId, requestGeneration)) return false;
          const reloaded = await reloadPublicationAfterConflict(requestGeneration);
          if (!isCurrentProject(projectId, requestGeneration)) return false;
          if (reloaded !== undefined) {
            setPublicationError(
              'Публикация изменилась в другой сессии. Данные обновлены — проверьте их и повторите действие.',
            );
            setPublicationPhase(reloaded ? 'install' : 'publish_error');
          } else {
            setPublicationError(
              'Публикация изменилась в другой сессии, но не удалось обновить её состояние. Обновите страницу перед повторной публикацией.',
            );
            setPublicationPhase(existingPublication ? 'install' : 'publish_error');
          }
        } else {
          setPublicationError(publicationFailureMessage(caught));
          setPublicationPhase(existingPublication ? 'install' : 'publish_error');
        }
        return false;
      } finally {
        if (isCurrentProject(projectId, requestGeneration)) {
          setPublicationPending(false);
        }
      }
    })();
    publishInFlightRef.current = inFlight;
    void inFlight.finally(() => {
      if (publishInFlightRef.current === inFlight) publishInFlightRef.current = null;
    });
    return inFlight;
  }, [
    artifactId,
    csrfToken,
    isCurrentProject,
    projectId,
    projectVersionId,
    reloadPublicationAfterConflict,
    revision,
    versionsEnabled,
  ]);

  const continueToFirstPublication = useCallback(async () => {
    if (!projectId || firstPublishAttemptedRef.current.has(projectId)) return false;
    firstPublishAttemptedRef.current.add(projectId);
    setPublicationPhase('checking');
    try {
      const restored = await ensurePublicationRecovered();
      if (restored) {
        setPublicationPhase('install');
        return true;
      }
      return await publish(null);
    } catch {
      return false;
    }
  }, [ensurePublicationRecovered, projectId, publish]);

  const claimFounder = async () => {
    if (!csrfToken || !projectId || working || publishInFlightRef.current) return;
    const requestGeneration = projectGenerationRef.current;
    setState('creating');
    setPublicationPhase('activating');
    setOfferError(null);
    try {
      const result = await claimFounderAccess(projectId, csrfToken);
      if (!isCurrentProject(projectId, requestGeneration)) return;
      setSubscription(result.subscription);
      billingValidatedProjectRef.current = projectId;
      setFounderFeedbackProjectId(projectId);
      setState('active');
      await continueToFirstPublication();
    } catch {
      if (!isCurrentProject(projectId, requestGeneration)) return;
      setState('idle');
      setPublicationPhase('access');
      setOfferError('Founder-доступ уже занят или сейчас недоступен. Выберите тариф или повторите позже.');
    }
  };

  useEffect(() => {
    if (
      state !== 'active'
      || !projectId
      || billingValidatedProjectRef.current !== projectId
    ) return;
    void continueToFirstPublication();
  }, [continueToFirstPublication, projectId, state]);

  const rollbackTarget = priorReleases.at(-1) ?? null;
  const versionOrdinal = (versionId: string | null | undefined) => {
    if (!versionId) return undefined;
    return projectVersions.find(({ id }) => id === versionId)?.ordinal
      ?? (versionId === projectVersionId ? projectVersionOrdinal : undefined);
  };
  const publishedVersionOrdinal = versionOrdinal(publication?.project_version_id);
  const rollbackVersionOrdinal = versionOrdinal(rollbackTarget?.project_version_id);
  const rollback = async () => {
    if (
      !csrfToken
      || !publication
      || !rollbackTarget
      || publicationPending
      || !publicationRestored
    ) return;
    const requestGeneration = projectGenerationRef.current;
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
      if (!isCurrentProject(projectId, requestGeneration)) return;
      setPriorReleases((known) => [
        ...known.filter(({ release_id }) => release_id !== rollbackTarget.release_id),
        current,
      ]);
      setPublication(restored);
    } catch (caught) {
      if (!isCurrentProject(projectId, requestGeneration)) return;
      if (
        caught instanceof BuilderApiError
        && caught.status === 409
        && caught.code === 'publication_conflict'
      ) {
        const reloaded = await reloadPublicationAfterConflict(requestGeneration);
        if (!isCurrentProject(projectId, requestGeneration)) return;
        setPublicationError(reloaded
          ? 'Публикация изменилась в другой сессии. Данные обновлены — проверьте их и повторите действие.'
          : 'Публикация изменилась в другой сессии. Обновите страницу перед повтором.');
      } else {
        setPublicationError('Не удалось откатить публикацию. Попробуйте ещё раз.');
      }
    } finally {
      if (isCurrentProject(projectId, requestGeneration)) {
        setPublicationPending(false);
      }
    }
  };

  const embedSnippet = publication
    ? `<script src="${publication.embed_url}" async></script>`
    : null;
  const renewalStatus = subscription?.next_charge
    ? `Следующее списание — ${rubles(subscription.next_charge.amount_minor)} ${subscriptionEndLabel(subscription.next_charge.at)}. Автопродление включено.`
    : subscription?.auto_renew
      ? `Следующее продление — ${subscriptionEndLabel(subscription.next_renewal_at ?? subscription.current_period_end)}. Автопродление включено.`
      : `Тариф действует до ${subscriptionEndLabel(subscription?.current_period_end)}. Автопродление выключено.`;

  const openSupport = () => {
    setSupportError(null);
    setSupportSent(false);
    setSupportOpen(true);
  };

  const copyText = async (value: string, target: Exclude<CopyTarget, null>) => {
    const requestGeneration = projectGenerationRef.current;
    const requestSequence = ++copyRequestSequenceRef.current;
    setCopiedTarget(null);
    setCopyError(false);
    if (!navigator.clipboard?.writeText) {
      setCopyError(true);
      return;
    }
    try {
      await navigator.clipboard.writeText(value);
      if (
        !isCurrentProject(projectId, requestGeneration)
        || copyRequestSequenceRef.current !== requestSequence
      ) return;
      setCopiedTarget(target);
    } catch {
      if (
        !isCurrentProject(projectId, requestGeneration)
        || copyRequestSequenceRef.current !== requestSequence
      ) return;
      setCopyError(true);
    }
  };

  return (
    <>
      {!csrfToken && (
        <aside id="studio-publication" className="studio-upgrade" aria-labelledby="studio-upgrade-title">
          <Sparkle aria-hidden size={22} weight="fill" />
          <div>
            <h2 id="studio-upgrade-title">Подключите виджет к сайту</h2>
            <p>Войдите в аккаунт, чтобы выбрать доступ и опубликовать проверенную версию.</p>
          </div>
        </aside>
      )}

      {csrfToken && recoveryFailed && (
        <aside id="studio-publication" className="studio-upgrade" aria-labelledby="studio-upgrade-title">
          <Sparkle aria-hidden size={22} weight="fill" />
          <div>
            <h2 id="studio-upgrade-title">Не удалось проверить доступ</h2>
            {error && <p className="studio-upgrade__error" role="alert">{error}</p>}
            <button type="button" onClick={retryRecovery}>
              <ArrowRight aria-hidden size={18} /> Повторить проверку
            </button>
          </div>
        </aside>
      )}

      {csrfToken && !recoveryFailed && !active && publicationPhase === 'checking' && (
        <section className="studio-upgrade__progress" role="status" aria-labelledby="publication-progress-title">
          <Clock aria-hidden size={22} />
          <div>
            <h2 id="publication-progress-title">Проверяем доступ</h2>
            <p>Восстанавливаем тариф и незавершённую оплату.</p>
          </div>
        </section>
      )}

      {csrfToken && !recoveryFailed && active && projectId && publicationPhase === 'checking' && (
        <section className="studio-upgrade__progress" role="status" aria-labelledby="publication-progress-title">
          <Clock aria-hidden size={22} />
          <div>
            <h2 id="publication-progress-title">Восстанавливаем публикацию</h2>
            <p>Проверяем, есть ли уже опубликованная версия, прежде чем создавать новую.</p>
          </div>
        </section>
      )}

      {csrfToken && !recoveryFailed && !active && publicationPhase === 'access' && (
        <div id="studio-publication" className="studio-upgrade__access-flow">
          {offer?.founder.eligible && (
            <p className="studio-upgrade__access-intro">
              Founder-пилот даёт ранний доступ в обмен на честную обратную связь о работе виджета и нужных доработках.
            </p>
          )}
          <PublicationAccessView
            offer={offer ?? EMPTY_OFFER}
            loading={offerLoading || (!offer && !offerError)}
            busy={working}
            error={offerError ?? error}
            onFounder={() => void claimFounder()}
            onCheckout={(planCode, autoRenew) => void startCheckout(planCode, autoRenew)}
          />
          {offerError && !offerLoading && !offer && (
            <button type="button" onClick={retryOffer}>
              <ArrowRight aria-hidden size={18} /> Повторить загрузку условий
            </button>
          )}
          {working && (
            <p className="studio-upgrade__status" role="status">
              {state === 'creating'
                ? 'Создаём безопасную платёжную ссылку…'
                : 'Ожидаем подтверждение оплаты…'}
            </p>
          )}
          {checkoutUrl && (
            <a
              className="studio-upgrade__checkout"
              href={checkoutUrl}
              target="_blank"
              rel="noopener noreferrer"
            >
              Перейти к оплате <ArrowRight aria-hidden size={18} />
            </a>
          )}
        </div>
      )}

      {csrfToken && (publicationPhase === 'activating' || publicationPhase === 'publishing') && (
        <section className="studio-upgrade__progress" role="status" aria-labelledby="publication-progress-title">
          <Clock aria-hidden size={22} />
          <div>
            <span>ПУБЛИКАЦИЯ В ОДНОМ ОКНЕ</span>
            <h2 id="publication-progress-title">
              {publicationPhase === 'activating'
                ? 'Активируем доступ'
                : 'Публикуем проверенную версию'}
            </h2>
            <p>
              {publicationPhase === 'activating'
                ? 'Подключаем выбранные условия без карты и дополнительных шагов.'
                : 'Создаём постоянную ссылку и готовим код установки.'}
            </p>
          </div>
        </section>
      )}

      {csrfToken && active && !projectId && (
        <aside id="studio-publication" className="studio-upgrade" aria-labelledby="studio-upgrade-title">
          <CheckCircle aria-hidden size={22} weight="fill" />
          <div>
            <h2 id="studio-upgrade-title">Доступ подключён</h2>
            <p className="studio-upgrade__renewal-status">{renewalStatus}</p>
            {autoRenewError && <p className="studio-upgrade__error" role="alert">{autoRenewError}</p>}
            {subscription?.auto_renew && (
              <button type="button" onClick={() => void disableAutoRenew()} disabled={autoRenewPending}>
                {autoRenewPending ? <Clock aria-hidden size={18} /> : null}
                Отключить автопродление
              </button>
            )}
          </div>
        </aside>
      )}

      {csrfToken && active && projectId && publicationPhase === 'publish_error' && (
        <section
          id="studio-publication"
          className="studio-upgrade__publish-error"
          aria-labelledby="publication-error-title"
          aria-hidden={supportOpen || undefined}
        >
          <span>ДОСТУП УЖЕ ПОДКЛЮЧЁН</span>
          <h2 id="publication-error-title">Доступ подключён, публикация не завершена</h2>
          <p>Тариф сохранён. Повторная попытка не активирует доступ ещё раз и не создаст новую оплату.</p>
          <p className="studio-upgrade__renewal-status">{renewalStatus}</p>
          {publicationError && <p className="studio-upgrade__error" role="alert">{publicationError}</p>}
          <div className="studio-upgrade__actions">
            <button
              type="button"
              onClick={() => void publish(publication)}
              disabled={publicationPending || !publicationRestored}
              aria-label={publicationRestored
                ? undefined
                : 'Опубликовать виджет после восстановления данных'}
            >
              {publicationPending ? <Clock aria-hidden size={18} /> : <ArrowRight aria-hidden size={18} />}
              Повторить публикацию
            </button>
            <button type="button" onClick={openSupport}>
              Нужна помощь? Связаться с Kaigo
            </button>
            {subscription?.auto_renew && (
              <button type="button" onClick={() => void disableAutoRenew()} disabled={autoRenewPending}>
                {autoRenewPending ? <Clock aria-hidden size={18} /> : null}
                Отключить автопродление
              </button>
            )}
          </div>
        </section>
      )}

      {csrfToken && active && publication && publicationPhase === 'install' && embedSnippet && (
        <div id="studio-publication" aria-hidden={supportOpen || undefined}>
          {publicationError && <p className="studio-upgrade__error" role="alert">{publicationError}</p>}
          <PublicationInstallView
            publication={publication}
            versionOrdinal={versionsEnabled ? publishedVersionOrdinal : undefined}
            subscription={subscription}
            copiedTarget={copiedTarget}
            copyError={copyError}
            onCopyCode={() => void copyText(embedSnippet, 'code')}
            onCopyLink={() => void copyText(publication.embed_url, 'link')}
          />
          <div className="studio-upgrade__actions">
            <button
              type="button"
              onClick={() => void publish(publication)}
              disabled={publicationPending || !publicationRestored}
            >
              {publicationPending ? <Clock aria-hidden size={18} /> : <ArrowRight aria-hidden size={18} />}
              Обновить публикацию
            </button>
            {rollbackTarget && (
              <button
                type="button"
                onClick={() => void rollback()}
                disabled={publicationPending || !publicationRestored}
              >
                {versionsEnabled && rollbackVersionOrdinal
                  ? `Вернуть версию ${rollbackVersionOrdinal}`
                  : 'Вернуть предыдущую публикацию'}
              </button>
            )}
            {subscription?.auto_renew && (
              <button type="button" onClick={() => void disableAutoRenew()} disabled={autoRenewPending}>
                {autoRenewPending ? <Clock aria-hidden size={18} /> : null}
                Отключить автопродление
              </button>
            )}
            <button type="button" onClick={openSupport}>Связаться с Kaigo</button>
          </div>
        </div>
      )}

    <SupportDialog
      open={supportOpen}
      founder={founderFeedback}
      busy={supportPending}
      error={supportError}
      sent={supportSent}
      onClose={() => setSupportOpen(false)}
      onSubmit={(input) => void sendSupport(input)}
    />
    </>
  );
}
