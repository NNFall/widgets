import { ArrowRight, LockKey, SpinnerGap } from '@phosphor-icons/react';
import { type ReactNode, useEffect, useRef, useState } from 'react';

type SessionSnapshot = {
  enabled: boolean;
  authenticated: boolean;
  email?: string | null;
  pending_draft_id?: string | null;
  providers?: string[];
};

type GateState =
  | { status: 'loading' }
  | { status: 'open' }
  | { status: 'auth'; draftId: string | null; providers: string[] }
  | { status: 'error'; message: string };

function requestedSourceUrl() {
  return new URLSearchParams(window.location.search).get('url')?.trim() ?? '';
}

export function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<GateState>({ status: 'loading' });
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const controller = new AbortController();

    async function hydrate() {
      try {
        const response = await fetch('/api/auth/session', {
          credentials: 'include',
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`session:${response.status}`);
        const session = await response.json() as SessionSnapshot;
        if (!session.enabled || session.authenticated) {
          setState({ status: 'open' });
          return;
        }

        let draftId = session.pending_draft_id ?? null;
        const sourceUrl = requestedSourceUrl();
        if (!draftId && sourceUrl) {
          const draftResponse = await fetch('/api/drafts', {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: sourceUrl, brief: '' }),
            signal: controller.signal,
          });
          if (!draftResponse.ok) throw new Error(`draft:${draftResponse.status}`);
          const draft = await draftResponse.json() as { id: string };
          draftId = draft.id;
        }
        setState({
          status: 'auth',
          draftId,
          providers: session.providers?.length ? session.providers : ['google', 'yandex'],
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
  }, []);

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
        <section className="auth-gate__card" role="alert">
          <LockKey size={34} aria-hidden />
          <h1>Студия временно недоступна</h1>
          <p>Мы не запускаем дорогую генерацию без подтверждённой сессии. Обновите страницу через минуту.</p>
          <small>{state.message}</small>
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
        <p>
          Войдите один раз — ссылка на сайт уже сохранена. Kaigo создаст первую версию бесплатно,
          а платить нужно только если захотите доработать и опубликовать виджет.
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
        <small>Без пароля. Генерация начнётся только после входа.</small>
      </section>
    </main>
  );
}
