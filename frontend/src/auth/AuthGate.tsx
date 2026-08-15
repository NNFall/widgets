import { ArrowClockwise, ArrowRight, LockKey, SpinnerGap } from '@phosphor-icons/react';
import { type ReactNode, useEffect, useState } from 'react';

import { ensureLandingJourney } from '../shared/journey';
import { CONTACT_CONFIG, supportMailtoHref } from '../shared/contact';

type SessionSnapshot = {
  enabled: boolean;
  authenticated: boolean;
  email?: string | null;
  csrf_token?: string | null;
  pending_draft_id?: string | null;
  providers?: string[];
};

type GateState =
  | { status: 'loading' }
  | { status: 'open' }
  | { status: 'auth'; draftId: string | null; providers: string[]; authMessage: string | null }
  | { status: 'error'; message: string };

const OAUTH_ERROR_MESSAGES: Record<string, string> = {
  access_denied: 'Вход отменён. Выберите способ входа и попробуйте ещё раз.',
  provider_unavailable: 'Сервис входа временно недоступен. Попробуйте ещё раз.',
  identity_unverified: 'Не удалось подтвердить почту аккаунта. Попробуйте другой аккаунт.',
  oauth_failed: 'Не удалось завершить вход. Попробуйте ещё раз.',
};

function requestedDraftId() {
  return new URLSearchParams(window.location.search).get('draft')?.trim() ?? '';
}

function requestedAuthErrorMessage() {
  const code = new URLSearchParams(window.location.search).get('auth_error')?.trim();
  if (!code) return null;
  return OAUTH_ERROR_MESSAGES[code] ?? OAUTH_ERROR_MESSAGES.oauth_failed;
}

function safeErrorDetail(message: string) {
  const responseCode = message.match(/^(?:session|claim):(\d{3})$/)?.[1];
  if (responseCode) return `Код ответа сервиса: ${responseCode}`;
  if (message.startsWith('draft:')) return 'Не удалось подтвердить сохранённый черновик.';
  return 'Не удалось подтвердить текущую сессию.';
}

function SupportContact() {
  return (
    <p className="auth-gate__support">
      Если Studio не открывается, <a href={supportMailtoHref()}>Написать в поддержку: {CONTACT_CONFIG.supportEmail}</a>.
    </p>
  );
}

export function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<GateState>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();

    async function hydrate() {
      try {
        await ensureLandingJourney();
        const response = await fetch('/api/auth/session', {
          credentials: 'include',
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`session:${response.status}`);
        const session = await response.json() as SessionSnapshot;
        if (!session.enabled) {
          setState({ status: 'open' });
          return;
        }

        const requestedDraft = requestedDraftId();
        const pendingDraft = session.pending_draft_id ?? null;
        if (session.authenticated) {
          if (requestedDraft) {
            if (requestedDraft !== pendingDraft || !session.csrf_token) {
              throw new Error('draft:unbound');
            }
            const claimResponse = await fetch(
              `/api/drafts/${encodeURIComponent(requestedDraft)}/claim`,
              {
                method: 'POST',
                credentials: 'include',
                headers: { 'X-CSRF-Token': session.csrf_token },
                signal: controller.signal,
              },
            );
            if (!claimResponse.ok) throw new Error(`claim:${claimResponse.status}`);
            const claimed = await claimResponse.json() as { project?: { id?: unknown } };
            const projectId = claimed.project?.id;
            if (typeof projectId !== 'string' || !projectId) throw new Error('claim:invalid');
            window.history.replaceState({}, '', `/studio?project=${encodeURIComponent(projectId)}`);
          }
          setState({ status: 'open' });
          return;
        }
        const draftId = requestedDraft === pendingDraft ? requestedDraft : pendingDraft;
        setState({
          status: 'auth',
          draftId,
          providers: session.providers ?? [],
          authMessage: requestedAuthErrorMessage(),
        });
      } catch (error) {
        if (controller.signal.aborted) return;
        setState({
          status: 'error',
          message: error instanceof Error ? error.message : 'Не удалось проверить сессию',
        });
      }
    }

    void hydrate();
    return () => controller.abort();
  }, [attempt]);

  if (state.status === 'open') return <>{children}</>;

  if (state.status === 'loading') {
    return (
      <main className="auth-gate auth-gate--loading" aria-live="polite">
        <SpinnerGap size={28} aria-hidden /> Проверяем сессию…
      </main>
    );
  }

  if (state.status === 'error') {
    return (
      <main className="auth-gate">
        <section className="auth-gate__card auth-gate__card--error" role="alert">
          <LockKey size={34} aria-hidden />
          <h1>Студия сейчас не открылась</h1>
          <p>Ваши проекты и сохранённая работа в безопасности. Попробуйте подключиться ещё раз.</p>
          <SupportContact />
          <div className="auth-gate__recovery">
            <button
              type="button"
              onClick={() => {
                setState({ status: 'loading' });
                setAttempt((value) => value + 1);
              }}
            >
              <ArrowClockwise aria-hidden size={18} />
              Повторить
            </button>
            <a href="/">Вернуться на главную</a>
          </div>
          <details className="auth-gate__details">
            <summary>Технические детали</summary>
            <p>{safeErrorDetail(state.message)}</p>
          </details>
        </section>
      </main>
    );
  }

  const suffix = state.draftId ? `?draft_id=${encodeURIComponent(state.draftId)}` : '';
  return (
    <main className="auth-gate">
      <section className="auth-gate__card">
        <span className="auth-gate__eyebrow">Бесплатная экспресс-версия</span>
        <h1>Сначала сохраните результат</h1>
        {state.authMessage && <p role="alert">{state.authMessage}</p>}
        <p>
          Войдите один раз — ссылка на сайт уже сохранена. Kaigo создаст первую версию бесплатно,
          а платить нужно только за публикацию и подключение готового виджета.
        </p>
        <div className="auth-gate__actions">
          {state.providers.includes('google') && (
            <a href={`/api/auth/google/start${suffix}`}>
              Продолжить с Google <ArrowRight size={18} aria-hidden />
            </a>
          )}
          {state.providers.includes('yandex') && (
            <a href={`/api/auth/yandex/start${suffix}`}>
              Продолжить с Яндексом <ArrowRight size={18} aria-hidden />
            </a>
          )}
        </div>
        {state.providers.length === 0 && (
          <>
            <p role="status">Вход временно недоступен</p>
            <SupportContact />
          </>
        )}
        <small>Без пароля. Генерация начнётся только после входа.</small>
      </section>
    </main>
  );
}
