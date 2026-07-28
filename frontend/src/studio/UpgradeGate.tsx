import { ArrowRight, CheckCircle, Clock, Sparkle } from '@phosphor-icons/react';
import { useEffect, useRef, useState } from 'react';

import {
  BuilderApiError,
  createBillingCheckout,
  getBillingPayment,
  getPendingBillingPayment,
  getBillingSubscription,
  resumeBillingPayment,
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
  pollIntervalMs?: number;
  maxPollAttempts?: number;
}

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
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
  maxPollAttempts = DEFAULT_MAX_POLL_ATTEMPTS,
}: UpgradeGateProps) {
  const [state, setState] = useState<UpgradeState>(csrfToken ? 'checking' : 'idle');
  const [paymentId, setPaymentId] = useState<string | null>(null);
  const [checkoutUrl, setCheckoutUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [recoveryAttempt, setRecoveryAttempt] = useState(0);
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

  return (
    <aside className="studio-upgrade" aria-labelledby="studio-upgrade-title">
      {active
        ? <CheckCircle aria-hidden size={22} weight="fill" />
        : <Sparkle aria-hidden size={22} weight="fill" />}
      <div>
        <h2 id="studio-upgrade-title">
          {active ? 'Тариф активирован' : 'Бесплатный результат готов'}
        </h2>
        <p>
          {active
            ? 'Оплата подтверждена, подписка сохранена в аккаунте.'
            : 'Он останется доступен в проекте. Для дальнейшей доработки и публикации понадобится тариф.'}
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
      </div>
      {active ? (
        <p className="studio-upgrade__active" role="status">
          <CheckCircle aria-hidden size={18} weight="fill" /> Оплата подтверждена
        </p>
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
          {state === 'checking' ? 'Проверяем тариф' : working ? 'Проверяем оплату' : 'Доработать и опубликовать'}
        </button>
      )}
    </aside>
  );
}
