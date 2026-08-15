import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const sdk = vi.hoisted(() => {
  const handlers = new Map<string, (payload: unknown) => void>();
  const widget = {
    close: vi.fn(),
    on: vi.fn((event: string, handler: (payload: unknown) => void) => {
      handlers.set(event, handler);
      return widget;
    }),
    render: vi.fn(() => widget),
  };
  return {
    configInit: vi.fn(),
    handlers,
    widget,
  };
});

vi.mock('@vkid/sdk', () => ({
  Config: { init: sdk.configInit },
  ConfigAuthMode: { Redirect: 'redirect' },
  ConfigResponseMode: { Redirect: 'redirect' },
  ConfigSource: { LOWCODE: 'lowcode' },
  OneTap: class {
    constructor() {
      return sdk.widget;
    }
  },
  OneTapContentId: { SIGN_IN: 0 },
  OneTapSkin: { Primary: 'primary' },
  Scheme: { LIGHT: 'light' },
  WidgetEvents: { ERROR: 'common: error' },
}));

import { VkOneTap } from './VkOneTap';

afterEach(() => {
  cleanup();
  sdk.configInit.mockReset();
  sdk.handlers.clear();
  sdk.widget.close.mockReset();
  sdk.widget.on.mockClear();
  sdk.widget.render.mockClear();
  vi.unstubAllGlobals();
});

it('renders one generic official VK ID row from a server-bound PKCE transaction', async () => {
  const onSettled = vi.fn();
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
    app_id: 54_721_213,
    redirect_uri: 'https://kaigo.space/api/auth/vk/callback',
    state: 'one-time-state',
    code_verifier: 'one-time-verifier',
  })));
  vi.stubGlobal('fetch', fetchMock);

  const view = render(<VkOneTap draftId="draft-1" onSettled={onSettled} />);

  await waitFor(() => expect(sdk.configInit).toHaveBeenCalledWith({
    app: 54_721_213,
    redirectUrl: 'https://kaigo.space/api/auth/vk/callback',
    state: 'one-time-state',
    codeVerifier: 'one-time-verifier',
    scope: 'email',
    mode: 'redirect',
    responseMode: 'redirect',
    source: 'lowcode',
  }));
  expect(fetchMock).toHaveBeenCalledWith(
    '/api/auth/vk/bootstrap?draft_id=draft-1',
    expect.objectContaining({ method: 'POST', credentials: 'include' }),
  );
  expect(sdk.widget.render).toHaveBeenCalledWith({
    container: expect.any(HTMLElement),
    scheme: 'light',
    skin: 'primary',
    contentId: 0,
    fastAuthEnabled: false,
    showAlternativeLogin: false,
    styles: { height: 44, borderRadius: 8 },
  });
  expect(screen.queryByRole('link', { name: 'Войти с VK ID' })).not.toBeInTheDocument();
  expect(onSettled).toHaveBeenCalledOnce();

  view.unmount();
  expect(sdk.widget.close).toHaveBeenCalledOnce();
});

it('falls back to the ordinary VK route when bootstrap or widget loading fails', async () => {
  const onSettled = vi.fn();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 503 })));

  render(<VkOneTap draftId="draft-fallback" onSettled={onSettled} />);

  expect(await screen.findByRole('link', { name: 'Войти с VK ID' })).toHaveAttribute(
    'href',
    '/api/auth/vk/start?draft_id=draft-fallback',
  );
  expect(onSettled).toHaveBeenCalledOnce();
});

it('switches to the fallback when the official iframe reports an error', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
    app_id: 54_721_213,
    redirect_uri: 'https://kaigo.space/api/auth/vk/callback',
    state: 'one-time-state',
    code_verifier: 'one-time-verifier',
  }))));
  render(<VkOneTap draftId={null} onSettled={vi.fn()} />);
  await waitFor(() => expect(sdk.widget.render).toHaveBeenCalledOnce());

  await act(async () => {
    sdk.handlers.get('common: error')?.({ code: 0 });
  });

  expect(screen.getByRole('link', { name: 'Войти с VK ID' })).toHaveAttribute(
    'href',
    '/api/auth/vk/start',
  );
});
