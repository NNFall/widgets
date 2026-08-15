import {
  Config,
  ConfigAuthMode,
  ConfigResponseMode,
  ConfigSource,
  OneTap,
  OneTapContentId,
  OneTapSkin,
  Scheme,
  WidgetEvents,
} from '@vkid/sdk';
import { useEffect, useRef, useState } from 'react';

type VkBootstrap = {
  app_id: number;
  redirect_uri: string;
  state: string;
  code_verifier: string;
};

function isVkBootstrap(value: unknown): value is VkBootstrap {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<VkBootstrap>;
  return Number.isSafeInteger(candidate.app_id)
    && Number(candidate.app_id) > 0
    && typeof candidate.redirect_uri === 'string'
    && candidate.redirect_uri.startsWith('https://')
    && new URL(candidate.redirect_uri).pathname === '/api/auth/vk/callback'
    && typeof candidate.state === 'string'
    && candidate.state.length > 0
    && typeof candidate.code_verifier === 'string'
    && candidate.code_verifier.length > 0;
}

function vkAuthPath(draftId: string | null, endpoint: 'bootstrap' | 'start') {
  const suffix = draftId ? `?draft_id=${encodeURIComponent(draftId)}` : '';
  return `/api/auth/vk/${endpoint}${suffix}`;
}

export function VkOneTap({
  draftId,
  onSettled,
}: {
  draftId: string | null;
  onSettled: () => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const onSettledRef = useRef(onSettled);
  const [fallback, setFallback] = useState(false);

  useEffect(() => {
    onSettledRef.current = onSettled;
  }, [onSettled]);

  useEffect(() => {
    const controller = new AbortController();
    let widget: OneTap | null = null;
    let mounted = true;

    async function renderOneTap() {
      try {
        const response = await fetch(vkAuthPath(draftId, 'bootstrap'), {
          method: 'POST',
          credentials: 'include',
          headers: { Accept: 'application/json' },
          signal: controller.signal,
        });
        if (!response.ok) throw new Error('vk bootstrap failed');
        const bootstrap: unknown = await response.json();
        if (!isVkBootstrap(bootstrap)) throw new Error('vk bootstrap is invalid');
        if (!mounted || !containerRef.current) return;

        Config.init({
          app: bootstrap.app_id,
          redirectUrl: bootstrap.redirect_uri,
          state: bootstrap.state,
          codeVerifier: bootstrap.code_verifier,
          scope: 'email',
          mode: ConfigAuthMode.Redirect,
          responseMode: ConfigResponseMode.Redirect,
          source: ConfigSource.LOWCODE,
        });
        widget = new OneTap();
        widget.on(WidgetEvents.ERROR, () => {
          if (!mounted) return;
          widget?.close();
          setFallback(true);
        });
        widget.render({
          container: containerRef.current,
          scheme: Scheme.LIGHT,
          skin: OneTapSkin.Primary,
          contentId: OneTapContentId.SIGN_IN,
          fastAuthEnabled: false,
          showAlternativeLogin: false,
          styles: { height: 44, borderRadius: 8 },
        });
        onSettledRef.current();
      } catch {
        if (!controller.signal.aborted && mounted) {
          setFallback(true);
          onSettledRef.current();
        }
      }
    }

    void renderOneTap();
    return () => {
      mounted = false;
      controller.abort();
      widget?.close();
      containerRef.current?.replaceChildren();
    };
  }, [draftId]);

  if (fallback) {
    return (
      <a className="auth-gate__vk-fallback" href={vkAuthPath(draftId, 'start')}>
        Войти с VK ID
      </a>
    );
  }

  return (
    <div
      ref={containerRef}
      className="auth-gate__vk-one-tap"
      aria-label="Войти с VK ID"
    />
  );
}
