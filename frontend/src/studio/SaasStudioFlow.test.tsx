import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { StudioPage } from './StudioPage';
import type { SaasProject, SaasRunSnapshot } from './types';
import { adaptSaasRunSnapshot, isSaasRunRegression } from './useBuilderRun';

const PROJECT_ID = '5d34bcab-cfd3-4ca2-8398-eb59f34aab92';
const SECOND_PROJECT_ID = '840ba06f-5bf8-4fd7-af64-d93d0b8a5e11';
const RUN_ID = 'a7c3081e-936c-41fc-85c0-e3484484c726';

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function sessionResponse() {
  return jsonResponse({
    enabled: true,
    authenticated: true,
    user_id: 42,
    email: 'owner@example.com',
    csrf_token: 'csrf-for-studio',
    pending_draft_id: null,
    providers: ['google'],
  });
}

function project(activeRun: Record<string, unknown> | null = null) {
  return {
    id: PROJECT_ID,
    tenant_id: 7,
    owner_user_id: 42,
    source_url: 'https://example.com',
    brief: 'Спокойный консультант по услугам',
    status: activeRun ? activeRun.status : 'draft',
    active_revision: null,
    active_version_id: activeRun?.status === 'completed' ? 'version-1' : null,
    active_run: activeRun,
    created_at: '2026-07-28T10:00:00+00:00',
    updated_at: '2026-07-28T10:00:00+00:00',
  };
}

function run(overrides: Record<string, unknown> = {}) {
  return {
    id: RUN_ID,
    project_id: PROJECT_ID,
    mode: 'express',
    status: 'queued',
    state: 'queued',
    progress: 0,
    current_stage: null,
    last_completed_stage: null,
    error_code: null,
    error_message: null,
    created_at: '2026-07-28T10:00:00+00:00',
    started_at: null,
    finished_at: null,
    latest_sequence: 1,
    events: [],
    preview: null,
    ...overrides,
  };
}

function versionArtifact(
  id: string,
  revision: number,
  artDirection = `Концепция ${id}`,
) {
  return {
    id,
    schema_version: '1',
    revision,
    stage: 'validation',
    art_direction: artDirection,
    body_html: `<main>${id}</main>`,
    css: '',
    javascript: '',
    theme_tokens: {},
    suggested_actions: [],
    change_summary: '',
    layout_contract: {},
    quality_status: 'verified',
    source: 'artifact',
  };
}

function emptyEventStream() {
  return new Response(new ReadableStream({
    start(controller) {
      controller.close();
    },
  }), { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
}

function eventStream(...events: Array<Record<string, unknown>>) {
  const body = events
    .map((event) => `id: ${event.sequence}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`)
    .join('');
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => { resolve = next; });
  return { promise, resolve };
}

beforeEach(() => {
  localStorage.clear();
  window.history.replaceState({}, '', `/studio?project=${PROJECT_ID}`);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('durable SaaS Studio flow', () => {
  it('preserves backend stage progress in the adapted Studio snapshot', () => {
    const owner = project() as SaasProject;
    const source = run({
      status: 'running',
      state: 'running',
      current_stage: 'conversation',
      last_completed_stage: 'identity',
    }) as SaasRunSnapshot;

    expect(adaptSaasRunSnapshot(owner, source)).toMatchObject({
      current_stage: 'conversation',
      last_completed_stage: 'identity',
    });
  });

  it('rejects a non-terminal snapshot after the same run became terminal', () => {
    const completed = run({
      status: 'completed',
      state: 'completed',
      progress: 100,
      latest_sequence: 4,
    }) as SaasRunSnapshot;
    const staleRunning = run({
      status: 'running',
      state: 'running',
      progress: 72,
      latest_sequence: 4,
    }) as SaasRunSnapshot;

    expect(isSaasRunRegression(completed, staleRunning, 4)).toBe(true);
    expect(isSaasRunRegression(completed, {
      ...staleRunning,
      id: 'replacement-run',
    }, 4)).toBe(false);
  });

  it('creates a new owned project from an empty Studio without calling legacy Builder', async () => {
    window.history.replaceState(
      {},
      '',
      '/studio?url=https%3A%2F%2Ffresh.example.com&utm_source=telegram&utm_medium=social&utm_campaign=launch&utm_term=widgets&utm_content=studio&email=private%40example.com&utm_unknown=discard',
    );
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (url === '/api/auth/session') return sessionResponse();
      if (url === '/api/projects' && !init?.method) return jsonResponse({ projects: [] });
      if (url === '/api/projects' && init?.method === 'POST') return jsonResponse(project(), 201);
      if (url === `/api/projects/${PROJECT_ID}`) return jsonResponse(project());
      throw new Error(`unexpected request: ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();

    render(<StudioPage />);

    expect(await screen.findByRole('heading', { name: 'Мои виджеты' })).toBeVisible();
    expect(screen.getByRole('heading', { name: 'Создайте новый виджет' })).toBeVisible();
    expect(screen.getByLabelText('Ссылка на сайт')).toHaveValue('https://fresh.example.com');
    await user.type(
      screen.getByRole('textbox', { name: 'Пожелание к AI-виджету' }),
      'Спокойный консультант',
    );
    await user.click(screen.getByRole('button', { name: 'Создать проект' }));

    await waitFor(() => expect(requests.some(({ url, init }) => url === '/api/projects' && init?.method === 'POST')).toBe(true));
    const create = requests.find(({ url, init }) => url === '/api/projects' && init?.method === 'POST')!;
    const headers = new Headers(create.init?.headers);
    expect(create.init?.method).toBe('POST');
    expect(headers.get('X-CSRF-Token')).toBe('csrf-for-studio');
    expect(JSON.parse(String(create.init?.body))).toEqual({
      url: 'https://fresh.example.com/',
      brief: 'Спокойный консультант',
      campaign: {
        utm_source: 'telegram',
        utm_medium: 'social',
        utm_campaign: 'launch',
        utm_term: 'widgets',
        utm_content: 'studio',
      },
    });
    expect(requests.some(({ url }) => url.includes('/builder'))).toBe(false);
    await waitFor(() => expect(new URLSearchParams(window.location.search).get('project')).toBe(PROJECT_ID));
  });

  it('opens a library project and follows browser navigation back to the Studio home', async () => {
    window.history.replaceState({}, '', '/studio');
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/projects') return jsonResponse({ projects: [project()] });
      if (url === '/api/auth/session') return sessionResponse();
      if (url === `/api/projects/${PROJECT_ID}`) return jsonResponse(project());
      if (url === `/api/projects/${PROJECT_ID}/versions`) {
        return jsonResponse({ active_version_id: null, versions: [] });
      }
      throw new Error(`unexpected request: ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();

    render(<StudioPage />);

    expect(await screen.findByText('example.com')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Открыть' }));

    await waitFor(() => expect(new URLSearchParams(window.location.search).get('project')).toBe(PROJECT_ID));
    expect(await screen.findByText('Проект готов к запуску')).toBeVisible();
    expect(screen.getByDisplayValue('https://example.com')).toBeVisible();

    window.history.pushState({}, '', '/studio');
    act(() => window.dispatchEvent(new PopStateEvent('popstate')));
    expect(await screen.findByRole('heading', { name: 'Мои виджеты' })).toBeVisible();
  });

  it('clears the previous run before hydrating a different project', async () => {
    const running = run({ status: 'running', state: 'running', progress: 38 });
    const secondProject = {
      ...project(),
      id: SECOND_PROJECT_ID,
      source_url: 'https://second.example.org',
      brief: 'Консультант второго проекта',
    };
    const requests: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      requests.push(url);
      if (url === '/api/auth/session') return sessionResponse();
      if (url === `/api/projects/${PROJECT_ID}`) return jsonResponse(project(running));
      if (url === `/api/runs/${RUN_ID}`) return jsonResponse(running);
      if (url === `/api/runs/${RUN_ID}/events`) return emptyEventStream();
      if (url === `/api/projects/${PROJECT_ID}/versions`) {
        return jsonResponse({ active_version_id: null, versions: [] });
      }
      if (url === `/api/projects/${SECOND_PROJECT_ID}`) return jsonResponse(secondProject);
      if (url === `/api/projects/${SECOND_PROJECT_ID}/versions`) {
        return jsonResponse({ active_version_id: null, versions: [] });
      }
      throw new Error(`unexpected request: ${url}`);
    }));

    render(<StudioPage />);
    expect(await screen.findByRole('button', { name: 'Отменить генерацию' })).toBeEnabled();

    window.history.pushState({}, '', `/studio?project=${SECOND_PROJECT_ID}`);
    act(() => window.dispatchEvent(new PopStateEvent('popstate')));

    expect(await screen.findByRole('heading', { name: 'Создайте первый AI-виджет' })).toBeVisible();
    await waitFor(() => expect(screen.getByLabelText('Ссылка на сайт')).toHaveValue('https://second.example.org'));
    expect(screen.queryByRole('button', { name: 'Отменить генерацию' })).not.toBeInTheDocument();
    expect(screen.queryByText('Создаём ваш виджет')).not.toBeInTheDocument();
    expect(requests).not.toContain(`/api/runs/${RUN_ID}/cancel`);
  });

  it('restores an authenticated queued run from the project API after reload', async () => {
    const queued = run();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/auth/session') return sessionResponse();
      if (url === `/api/projects/${PROJECT_ID}`) return jsonResponse(project(queued));
      if (url === `/api/runs/${RUN_ID}`) return jsonResponse(queued);
      if (url === `/api/projects/${PROJECT_ID}/versions`) {
        return jsonResponse({ active_version_id: null, versions: [] });
      }
      if (url === `/api/runs/${RUN_ID}/events`) return emptyEventStream();
      throw new Error(`unexpected request: ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<StudioPage />);

    expect((await screen.findAllByText('Запуск в очереди'))[0]).toBeVisible();
    const projectContext = screen.getByText('Контекст проекта').closest('details');
    expect(projectContext).not.toHaveAttribute('open');
    expect(within(projectContext!).getByText('https://example.com')).toBeInTheDocument();
    expect(fetchMock.mock.calls.slice(0, 3).map(([url]) => String(url))).toEqual([
      '/api/auth/session',
      `/api/projects/${PROJECT_ID}`,
      `/api/runs/${RUN_ID}`,
    ]);
    expect(localStorage.getItem('kaigo.builder.activeRun.v1')).toBeNull();
  });

  it('creates the first run with project API, CSRF, and one persistent idempotency key', async () => {
    const queued = run();
    const createdEvent = {
      sequence: 1,
      type: 'run.created',
      message: 'Generation queued',
      payload: { status: 'queued' },
      created_at: '2026-07-28T10:00:00+00:00',
    };
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (url === '/api/auth/session') return sessionResponse();
      if (url === `/api/projects/${PROJECT_ID}`) return jsonResponse(project());
      if (url === `/api/projects/${PROJECT_ID}/runs`) return jsonResponse(queued, 202);
      if (url === `/api/runs/${RUN_ID}/events`) return eventStream(createdEvent);
      if (url === `/api/runs/${RUN_ID}`) return jsonResponse(run({ events: [createdEvent] }));
      throw new Error(`unexpected request: ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();

    render(<StudioPage />);
    await user.click(await screen.findByRole('button', { name: 'Создать AI-виджет' }));

    await waitFor(() => expect(requests.some(({ url }) => url === `/api/projects/${PROJECT_ID}/runs`)).toBe(true));
    const create = requests.find(({ url }) => url === `/api/projects/${PROJECT_ID}/runs`)!;
    const headers = new Headers(create.init?.headers);
    expect(create.init?.method).toBe('POST');
    expect(headers.get('X-CSRF-Token')).toBe('csrf-for-studio');
    expect(headers.get('Idempotency-Key')).toMatch(/^[A-Za-z0-9._-]{16,128}$/);
    expect(JSON.parse(String(create.init?.body))).toEqual({ mode: 'express' });
    expect(requests.some(({ url }) => url.includes('/builder/api/runs'))).toBe(false);
    expect(localStorage.getItem(`kaigo.saas.project.${PROJECT_ID}.idempotency-key`)).toBe(headers.get('Idempotency-Key'));
    const streamRequest = requests.find(({ url }) => url === `/api/runs/${RUN_ID}/events`)!;
    expect(new Headers(streamRequest.init?.headers).get('Last-Event-ID')).toBeNull();
    expect(await screen.findByText('Готовим проект к запуску')).toBeInTheDocument();
  });

  it('lets the project owner request safe cancellation with session CSRF', async () => {
    const running = run({ status: 'running', state: 'running', progress: 37 });
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (url === '/api/auth/session') return sessionResponse();
      if (url === `/api/projects/${PROJECT_ID}`) return jsonResponse(project(running));
      if (url === `/api/runs/${RUN_ID}`) return jsonResponse(running);
      if (url === `/api/runs/${RUN_ID}/events`) return emptyEventStream();
      if (url === `/api/runs/${RUN_ID}/cancel`) {
        return jsonResponse({ run_id: RUN_ID, cancel_requested: true, status: 'running' }, 202);
      }
      throw new Error(`unexpected request: ${url}`);
    }));
    const user = userEvent.setup();

    render(<StudioPage />);
    await user.click(await screen.findByRole('button', { name: 'Отменить генерацию' }));

    await waitFor(() => expect(requests.some(({ url }) => url === `/api/runs/${RUN_ID}/cancel`)).toBe(true));
    const cancellation = requests.find(({ url }) => url === `/api/runs/${RUN_ID}/cancel`)!;
    expect(cancellation.init?.method).toBe('POST');
    expect(new Headers(cancellation.init?.headers).get('X-CSRF-Token')).toBe('csrf-for-studio');
    expect((await screen.findAllByText('Отмена запрошена — генерация остановится безопасно'))[0]).toBeVisible();
  });

  it('adopts one idempotent compensated retry and keeps the replacement durable', async () => {
    const failed = run({
      status: 'failed',
      state: 'failed',
      error_code: 'provider_unavailable',
      error_message: 'Provider unavailable',
      latest_sequence: 1,
      events: [{
        sequence: 1,
        type: 'run.failed',
        message: 'Старая ошибка предыдущего запуска',
        payload: { status: 'failed', error_code: 'provider_unavailable' },
        created_at: '2026-07-28T10:00:00+00:00',
      }],
    });
    const replacement = run({
      id: '2d44e96d-6e52-43b7-ada4-dc88d625a9e7',
      status: 'queued',
      state: 'queued',
      error_code: null,
      error_message: null,
      latest_sequence: 0,
      events: undefined,
    });
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (url === '/api/auth/session') return sessionResponse();
      if (url === `/api/projects/${PROJECT_ID}`) return jsonResponse(project(failed));
      if (url === `/api/runs/${RUN_ID}`) return jsonResponse(failed);
      if (url === `/api/runs/${RUN_ID}/retry`) return jsonResponse(replacement, 202);
      if (url === `/api/runs/${replacement.id}/events`) return emptyEventStream();
      if (url === `/api/runs/${replacement.id}`) return jsonResponse(replacement);
      throw new Error(`unexpected request: ${url}`);
    }));
    const user = userEvent.setup();

    render(<StudioPage />);
    const timeline = (await screen.findByText('Технические детали')).closest('details');
    expect(timeline).not.toBeNull();
    expect(await within(timeline!).findByText('Создание остановлено — можно повторить запуск')).toBeInTheDocument();
    await user.click(await screen.findByRole('button', { name: 'Повторить запуск' }));

    await waitFor(() => expect(requests.some(({ url }) => url === `/api/runs/${RUN_ID}/retry`)).toBe(true));
    const retry = requests.find(({ url }) => url === `/api/runs/${RUN_ID}/retry`)!;
    const headers = new Headers(retry.init?.headers);
    expect(retry.init?.method).toBe('POST');
    expect(headers.get('X-CSRF-Token')).toBe('csrf-for-studio');
    expect(headers.get('Idempotency-Key')).toBe(`studio-retry-${RUN_ID}`);
    expect(localStorage.getItem(`kaigo.saas.run.${RUN_ID}.retry-idempotency-key`)).toBe(
      `studio-retry-${RUN_ID}`,
    );
    expect((await screen.findAllByText('Запуск в очереди'))[0]).toBeVisible();
    expect(within(timeline!).queryByText('Создание остановлено — можно повторить запуск')).not.toBeInTheDocument();
  });

  it('explains that a consumed free generation cannot be retried', async () => {
    const failed = run({ status: 'failed', state: 'failed', error_code: 'invalid_request' });
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/auth/session') return sessionResponse();
      if (url === `/api/projects/${PROJECT_ID}`) return jsonResponse(project(failed));
      if (url === `/api/runs/${RUN_ID}`) return jsonResponse(failed);
      if (url === `/api/runs/${RUN_ID}/retry`) {
        return jsonResponse({ error: { code: 'trial_consumed' } }, 409);
      }
      throw new Error(`unexpected request: ${url}`);
    }));
    const user = userEvent.setup();

    render(<StudioPage />);
    await user.click(await screen.findByRole('button', { name: 'Повторить запуск' }));

    expect(await screen.findByText('Бесплатная генерация уже использована. Сохранённый результат остаётся доступен.')).toBeVisible();
  });

  it('previews a restorable failed draft without claiming readiness or offering checkout', async () => {
    const preview = {
      schema_version: '1',
      revision: 2,
      stage: 'validation',
      art_direction: 'Тёплый интерфейс',
      body_html: '<main><h1>Бесплатный результат</h1></main>',
      css: 'h1 { color: #111; } </style><p>style escape</p>',
      javascript: '</script><p>script escape</p>',
      theme_tokens: {},
      suggested_actions: [],
      change_summary: 'Собран рабочий черновик',
      layout_contract: {},
      source: 'restorable_draft',
    };
    const failed = run({
      status: 'failed',
      state: 'failed',
      progress: 81,
      error_code: 'repair_budget_exhausted',
      error_message: 'Visual repair budget exhausted',
      preview,
    });
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/auth/session') return sessionResponse();
      if (url === `/api/projects/${PROJECT_ID}`) return jsonResponse(project(failed));
      if (url === `/api/runs/${RUN_ID}`) return jsonResponse(failed);
      if (url === `/api/runs/${RUN_ID}/events`) return emptyEventStream();
      if (url === '/api/billing/subscription') return jsonResponse({ subscription: null });
      if (url === '/api/billing/payments/pending') {
        return jsonResponse({ payment: null, checkout_url: null });
      }
      throw new Error(`unexpected request: ${url}`);
    }));

    render(<StudioPage />);

    const frame = await screen.findByTitle('Предпросмотр консультанта Kaigo');
    const previewUrl = new URL(frame.getAttribute('src')!);
    expect(previewUrl.pathname).toBe(`/api/runs/${RUN_ID}/preview/document`);
    expect(previewUrl.searchParams.get('revision')).toBe('2');
    expect(previewUrl.searchParams.get('channel')).toMatch(/^[a-f0-9]{36}$/);
    expect(frame).not.toHaveAttribute('srcdoc');
    expect(frame).toHaveAttribute('sandbox', 'allow-scripts');
    expect(screen.queryByText('Подключите виджет к сайту')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Опубликовать и подключить' })).not.toBeInTheDocument();
    expect(screen.queryByText(/тариф/i)).not.toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalledWith('/api/billing/subscription', expect.anything());
    expect(fetch).not.toHaveBeenCalledWith('/api/billing/payments/pending', expect.anything());
  });

  it('sends SaaS preview chat through the owner endpoint with session CSRF', async () => {
    const preview = {
      revision: 2,
      body_html: '<main>Runtime result</main>',
      css: '',
      javascript: '',
      quality_status: 'accepted',
      source: 'accepted_artifact',
    };
    const completed = run({
      status: 'completed',
      state: 'completed',
      progress: 100,
      preview,
    });
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (url === '/api/auth/session') return sessionResponse();
      if (url === `/api/projects/${PROJECT_ID}`) return jsonResponse(project(completed));
      if (url === `/api/runs/${RUN_ID}`) return jsonResponse(completed);
      if (url === `/api/runs/${RUN_ID}/chat`) {
        return jsonResponse({ request_id: 'request-saas-123', reply: 'Ответ сервера' });
      }
      throw new Error(`unexpected request: ${url}`);
    }));

    render(<StudioPage />);
    const frame = await screen.findByTitle('Предпросмотр консультанта Kaigo') as HTMLIFrameElement;
    const channel = new URL(frame.getAttribute('src')!).searchParams.get('channel');
    const postMessage = vi.spyOn(frame.contentWindow!, 'postMessage');

    await act(async () => {
      window.dispatchEvent(new MessageEvent('message', {
        source: frame.contentWindow,
        data: {
          source: 'kaigo-builder-preview',
          version: 2,
          channel_id: channel,
          type: 'chat.request',
          request_id: 'request-saas-123',
          revision: 2,
          text: 'Расскажите подробнее',
        },
      }));
    });

    await waitFor(
      () => expect(requests.some(({ url }) => url === `/api/runs/${RUN_ID}/chat`)).toBe(true),
      { timeout: 5_000 },
    );
    const chat = requests.find(({ url }) => url === `/api/runs/${RUN_ID}/chat`)!;
    const headers = new Headers(chat.init?.headers);
    expect(chat.init?.method).toBe('POST');
    expect(chat.init?.credentials).toBe('include');
    expect(headers.get('X-CSRF-Token')).toBe('csrf-for-studio');
    expect(JSON.parse(String(chat.init?.body))).toEqual({
      request_id: 'request-saas-123',
      message: 'Расскажите подробнее',
      revision: 2,
    });
    await waitFor(() => expect(postMessage).toHaveBeenCalledWith(expect.objectContaining({
      type: 'chat.response',
      request_id: 'request-saas-123',
      text: 'Ответ сервера',
    }), '*'));
  });

  it('ignores duplicate and out-of-order SSE sequences', async () => {
    const running = run({
      status: 'running',
      state: 'running',
      latest_sequence: 1,
      events: [{
        sequence: 1,
        type: 'run.created',
        message: 'Запуск создан',
        payload: { status: 'queued' },
        created_at: '2026-07-28T10:00:00+00:00',
      }],
    });
    const stream = [
      'id: 1\nevent: run.created\ndata: {"sequence":1,"type":"run.created","message":"Дубликат","payload":{"status":"queued"},"created_at":"2026-07-28T10:00:00Z"}\n\n',
      'id: 2\nevent: stage.started\ndata: {"sequence":2,"type":"stage.started","message":"Собираем основу","payload":{"status":"running","stage":"foundation"},"created_at":"2026-07-28T10:00:01Z"}\n\n',
      'id: 1\nevent: run.created\ndata: {"sequence":1,"type":"run.created","message":"Старое событие","payload":{"status":"queued"},"created_at":"2026-07-28T10:00:00Z"}\n\n',
    ].join('');
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/auth/session') return sessionResponse();
      if (url === `/api/projects/${PROJECT_ID}`) return jsonResponse(project(running));
      if (url === `/api/runs/${RUN_ID}`) return jsonResponse(running);
      if (url === `/api/runs/${RUN_ID}/events`) {
        expect(new Headers(init?.headers).get('Last-Event-ID')).toBe('1');
        return new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } });
      }
      throw new Error(`unexpected request: ${url}`);
    }));

    render(<StudioPage />);

    expect((await screen.findAllByText('Собираем основу будущего виджета')).length).toBeGreaterThan(0);
    expect(screen.queryByText('Дубликат')).not.toBeInTheDocument();
    expect(screen.queryByText('Старое событие')).not.toBeInTheDocument();
    expect(document.querySelectorAll('.studio-technical__body li')).toHaveLength(2);
  });

  it('keeps raw SSE diagnostics out of the visible Studio header', async () => {
    const running = run({ status: 'running', state: 'running', latest_sequence: 0 });
    const rawMessage = 'Gemini bytes=991; C:\\secret\\trace.log';
    const incoming = {
      sequence: 1,
      type: 'provider.telemetry',
      message: rawMessage,
      payload: { status: 'running', stage: 'foundation' },
      created_at: '2026-07-28T10:00:01Z',
    };
    let runReads = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/auth/session') return sessionResponse();
      if (url === `/api/projects/${PROJECT_ID}`) return jsonResponse(project(running));
      if (url === `/api/projects/${PROJECT_ID}/versions`) {
        return jsonResponse({ active_version_id: null, versions: [] });
      }
      if (url === `/api/runs/${RUN_ID}`) {
        runReads += 1;
        if (runReads === 1) return jsonResponse(running);
        return new Promise<Response>(() => undefined);
      }
      if (url === `/api/runs/${RUN_ID}/events`) return eventStream(incoming);
      throw new Error(`unexpected request: ${url}`);
    }));

    render(<StudioPage />);

    await waitFor(() => expect(
      document.querySelector('.studio-header__session strong'),
    ).toHaveTextContent('Собираем основу будущего виджета'));
    expect(screen.queryByText(rawMessage)).not.toBeInTheDocument();
  });

  it.each([
    [401, 'Войдите'],
    [403, 'Нет доступа'],
    [404, 'Проект не найден'],
    [409, 'Бесплатный запуск уже использован'],
    [503, 'Сервис временно недоступен'],
  ])('shows a Russian project error for HTTP %s', async (status, message) => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === '/api/auth/session') return sessionResponse();
      return jsonResponse({ error: { code: 'request_failed' } }, status);
    }));

    render(<StudioPage />);

    expect(await screen.findByRole('alert')).toHaveTextContent(message);
  });

  it.each([
    [401, 'authentication_required'],
    [403, 'csrf_failed'],
    [500, 'internal_error'],
  ])('fails closed when the version endpoint returns HTTP %s', async (status, code) => {
    const completed = run({
      status: 'completed',
      state: 'completed',
      progress: 100,
      preview: { ...versionArtifact('artifact-version-1', 1), source: 'accepted_artifact' },
    });
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/auth/session') return sessionResponse();
      if (url === `/api/projects/${PROJECT_ID}`) return jsonResponse(project(completed));
      if (url === `/api/runs/${RUN_ID}`) return jsonResponse(completed);
      if (url === `/api/projects/${PROJECT_ID}/versions`) {
        return jsonResponse({ error: { code, message: `version endpoint ${status}` } }, status);
      }
      if (url === '/api/billing/subscription') return jsonResponse({ subscription: null });
      if (url === '/api/billing/payments/pending') {
        return jsonResponse({ payment: null, checkout_url: null });
      }
      throw new Error(`unexpected request: ${url}`);
    }));

    render(<StudioPage />);

    expect(await screen.findByRole('alert')).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Опубликовать и подключить' })).not.toBeInTheDocument();
  });

  it('fails closed when the version endpoint has a network failure', async () => {
    const completed = run({
      status: 'completed',
      state: 'completed',
      progress: 100,
      preview: { ...versionArtifact('artifact-version-1', 1), source: 'accepted_artifact' },
    });
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/auth/session') return sessionResponse();
      if (url === `/api/projects/${PROJECT_ID}`) return jsonResponse(project(completed));
      if (url === `/api/runs/${RUN_ID}`) return jsonResponse(completed);
      if (url === `/api/projects/${PROJECT_ID}/versions`) {
        throw new TypeError('version endpoint disconnected');
      }
      if (url === '/api/billing/subscription') return jsonResponse({ subscription: null });
      if (url === '/api/billing/payments/pending') {
        return jsonResponse({ payment: null, checkout_url: null });
      }
      throw new Error(`unexpected request: ${url}`);
    }));

    render(<StudioPage />);

    expect(await screen.findByRole('alert')).toHaveTextContent(/версии проекта/i);
    expect(screen.queryByRole('button', { name: 'Опубликовать и подключить' })).not.toBeInTheDocument();
  });

  it('uses legacy artifact publication for a privacy-safe 404 after project hydration', async () => {
    const completed = run({
      status: 'completed',
      state: 'completed',
      progress: 100,
      preview: { ...versionArtifact('artifact-version-1', 1), source: 'accepted_artifact' },
    });
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/auth/session') return sessionResponse();
      if (url === `/api/projects/${PROJECT_ID}`) return jsonResponse(project(completed));
      if (url === `/api/runs/${RUN_ID}`) return jsonResponse(completed);
      if (url === `/api/projects/${PROJECT_ID}/versions`) {
        return jsonResponse({
          error: { code: 'not_found', message: 'Project versions unavailable' },
        }, 404);
      }
      if (url === '/api/billing/subscription') return jsonResponse({ subscription: null });
      if (url === '/api/billing/payments/pending') {
        return jsonResponse({ payment: null, checkout_url: null });
      }
      throw new Error(`unexpected request: ${url}`);
    }));

    const user = userEvent.setup();
    render(<StudioPage />);

    await user.click(await screen.findByRole('button', { name: 'Открыть публикацию' }));
    expect(await screen.findByRole('button', { name: 'Опубликовать и подключить' })).toBeVisible();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('falls back to polling when the authenticated SSE fetch fails', async () => {
    const running = run({ status: 'running', state: 'running' });
    const completed = run({ status: 'completed', state: 'completed', progress: 100 });
    let snapshots = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/auth/session') return sessionResponse();
      if (url === `/api/projects/${PROJECT_ID}`) return jsonResponse(project(running));
      if (url === `/api/runs/${RUN_ID}`) {
        snapshots += 1;
        return jsonResponse(snapshots > 1 ? completed : running);
      }
      if (url === `/api/runs/${RUN_ID}/events`) throw new TypeError('stream disconnected');
      throw new Error(`unexpected request: ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<StudioPage />);
    expect(await screen.findByText('Резервный режим обновления')).toBeVisible();
    expect((await screen.findAllByText('Готово — виджет сохранён', {}, { timeout: 3_000 }))[0]).toBeVisible();
  });

  it('keeps fallback polling single-flight while a slow request is pending', async () => {
    vi.useFakeTimers();
    const running = run({ status: 'running', state: 'running' });
    const slowPoll = deferred<Response>();
    let runRequests = 0;
    let activePolls = 0;
    let maximumConcurrentPolls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/auth/session') return sessionResponse();
      if (url === `/api/projects/${PROJECT_ID}`) return jsonResponse(project(running));
      if (url === `/api/runs/${RUN_ID}`) {
        runRequests += 1;
        if (runRequests === 1) return jsonResponse(running);
        activePolls += 1;
        maximumConcurrentPolls = Math.max(maximumConcurrentPolls, activePolls);
        return slowPoll.promise.finally(() => { activePolls -= 1; });
      }
      if (url === `/api/runs/${RUN_ID}/events`) throw new TypeError('stream disconnected');
      throw new Error(`unexpected request: ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<StudioPage />);
    await vi.waitFor(() => expect(screen.getByText('Резервный режим обновления')).toBeVisible());
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(runRequests).toBe(2);

    await act(async () => { await vi.advanceTimersByTimeAsync(8_000); });
    expect(runRequests).toBe(2);
    expect(maximumConcurrentPolls).toBe(1);

    slowPoll.resolve(jsonResponse(run({ status: 'completed', state: 'completed', progress: 100 })));
    await act(async () => { await Promise.resolve(); });
  });

  it.each([401, 404])('stops fallback polling permanently after nonretryable HTTP %s', async (status) => {
    vi.useFakeTimers();
    const running = run({ status: 'running', state: 'running' });
    let runRequests = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/auth/session') return sessionResponse();
      if (url === `/api/projects/${PROJECT_ID}`) return jsonResponse(project(running));
      if (url === `/api/runs/${RUN_ID}`) {
        runRequests += 1;
        return runRequests === 1
          ? jsonResponse(running)
          : jsonResponse({ error: { code: 'run_unavailable' } }, status);
      }
      if (url === `/api/runs/${RUN_ID}/events`) throw new TypeError('stream disconnected');
      throw new Error(`unexpected request: ${url}`);
    }));

    render(<StudioPage />);
    await vi.waitFor(() => expect(screen.getByText('Резервный режим обновления')).toBeVisible());
    await act(async () => { await vi.advanceTimersByTimeAsync(12_000); });

    expect(runRequests).toBe(2);
    expect(screen.queryByText('Резервный режим обновления')).not.toBeInTheDocument();
  });

  it.each([401, 404])('does not start polling when SSE returns HTTP %s', async (status) => {
    vi.useFakeTimers();
    const running = run({ status: 'running', state: 'running' });
    let runRequests = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/auth/session') return sessionResponse();
      if (url === `/api/projects/${PROJECT_ID}`) return jsonResponse(project(running));
      if (url === `/api/runs/${RUN_ID}`) {
        runRequests += 1;
        return jsonResponse(running);
      }
      if (url === `/api/runs/${RUN_ID}/events`) return jsonResponse({ error: 'not available' }, status);
      throw new Error(`unexpected request: ${url}`);
    }));

    render(<StudioPage />);
    await vi.waitFor(() => expect(screen.getByRole('alert')).toBeVisible());
    await act(async () => { await vi.advanceTimersByTimeAsync(12_000); });

    expect(runRequests).toBe(1);
    expect(screen.queryByText('Резервный режим обновления')).not.toBeInTheDocument();
  });

  it('aggregates usage deltas across every durable event', async () => {
    const completed = run({
      status: 'completed',
      state: 'completed',
      progress: 100,
      latest_sequence: 2,
      events: [
        {
          sequence: 1,
          type: 'stage.completed',
          message: 'Foundation complete',
          payload: { status: 'completed', stage: 'foundation', usage: { prompt_tokens: 10, output_tokens: 5, thinking_tokens: 1, total_tokens: 16 } },
          created_at: '2026-07-28T10:00:01+00:00',
        },
        {
          sequence: 2,
          type: 'stage.completed',
          message: 'Conversation complete',
          payload: { status: 'completed', stage: 'conversation', usage: { prompt_tokens: 20, output_tokens: 20, thinking_tokens: 4, total_tokens: 44 } },
          created_at: '2026-07-28T10:00:02+00:00',
        },
      ],
    });
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/auth/session') return sessionResponse();
      if (url === `/api/projects/${PROJECT_ID}`) return jsonResponse(project(completed));
      if (url === `/api/runs/${RUN_ID}`) return jsonResponse(completed);
      throw new Error(`unexpected request: ${url}`);
    }));

    const adapted = adaptSaasRunSnapshot(
      project(completed) as SaasProject,
      completed as SaasRunSnapshot,
    );
    expect(adapted.usage.total_tokens).toBe(60);
  });

  it('uses the server progress instead of estimating from event count', async () => {
    const running = run({ status: 'running', state: 'running', progress: 63, latest_sequence: 0 });
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/auth/session') return sessionResponse();
      if (url === `/api/projects/${PROJECT_ID}`) return jsonResponse(project(running));
      if (url === `/api/runs/${RUN_ID}`) return jsonResponse(running);
      if (url === `/api/runs/${RUN_ID}/events`) return emptyEventStream();
      throw new Error(`unexpected request: ${url}`);
    }));

    render(<StudioPage />);

    expect(await screen.findByText('63%')).toBeVisible();
  });

  it('shows an accepted artifact as ready', async () => {
    const completed = run({
      status: 'completed',
      state: 'completed',
      progress: 100,
      preview: {
        id: 'artifact-ready-3',
        revision: 3,
        body_html: '<main>Ready</main>',
        css: '',
        javascript: '',
        quality_status: 'accepted',
        source: 'accepted_artifact',
      },
    });
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/auth/session') return sessionResponse();
      if (url === `/api/projects/${PROJECT_ID}`) return jsonResponse(project(completed));
      if (url === `/api/runs/${RUN_ID}`) return jsonResponse(completed);
      if (url === `/api/projects/${PROJECT_ID}/versions`) {
        return jsonResponse({
          active_version_id: 'version-1',
          versions: [{
            id: 'version-1',
            project_id: PROJECT_ID,
            ordinal: 1,
            kind: 'initial',
            change_request: null,
            parent_version_id: null,
            run_id: RUN_ID,
            artifact_id: 'artifact-ready-3',
            artifact_revision: 3,
            refinable: true,
            created_at: '2026-07-28T10:00:00Z',
          }],
        });
      }
      if (url === '/api/artifacts/artifact-ready-3') {
        return jsonResponse(versionArtifact(
          'artifact-ready-3',
          3,
          'Проверенная активная концепция',
        ));
      }
      if (url === '/api/billing/subscription') return jsonResponse({ subscription: null });
      if (url === '/api/billing/payments/pending') {
        return jsonResponse({ payment: null, checkout_url: null });
      }
      throw new Error(`unexpected request: ${url}`);
    }));

    const user = userEvent.setup();
    render(<StudioPage />);

    const workbench = await screen.findByRole('main', { name: 'Рабочая студия' });
    expect(within(workbench).getByRole('complementary', { name: 'Чат с Kaigo' })).toBeVisible();
    const previewRegion = within(workbench).getByRole('region', { name: 'Предпросмотр виджета' });
    expect(previewRegion).toBeVisible();
    expect(within(previewRegion).getByRole('heading', { name: 'Предпросмотр' })).toBeVisible();
    expect(within(previewRegion).getByText('Версия 1')).toBeVisible();
    expect(within(previewRegion).getByText('Проверено')).toBeVisible();
    expect(screen.queryByLabelText('Состояние выбранной версии')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Что изменить в виджете?')).toBeVisible();
    expect(screen.queryByRole('region', { name: 'История версий' })).not.toBeInTheDocument();
    expect(screen.queryByText('Подключите виджет к сайту')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Открыть тариф и лимиты' }));
    const accountDialog = screen.getByRole('dialog', { name: 'Тариф и лимиты' });
    expect(within(accountDialog).getByText('Бесплатный режим')).toBeVisible();
    expect(within(accountDialog).getByText('Первая экспресс-версия — бесплатно')).toBeVisible();
    expect(within(accountDialog).getByText('Для продолжения нужен тариф')).toBeVisible();
    expect(within(accountDialog).getByText('После оплаты откроется')).toBeVisible();
    expect(within(accountDialog).getByText('Доработка новыми версиями')).toBeVisible();
    expect(within(accountDialog).getByText('Публикация на выбранных сайтах')).toBeVisible();
    expect(within(accountDialog).getByText('Код установки и безопасные обновления')).toBeVisible();
    await user.keyboard('{Escape}');

    await user.click(screen.getByRole('button', { name: 'Открыть версии' }));
    const versionsDialog = screen.getByRole('dialog', { name: 'История версий' });
    expect(within(versionsDialog).getByRole('region', { name: 'История версий' })).toBeVisible();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog', { name: 'История версий' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Открыть публикацию' }));
    const publicationDialog = screen.getByRole('dialog', { name: 'Публикация виджета' });
    expect(within(publicationDialog).getByText('Подключите виджет к сайту')).toBeVisible();
    expect(within(publicationDialog).getByRole('list', { name: 'Путь до запуска виджета' })).toBeVisible();
    expect(within(publicationDialog).getByText('Тариф')).toBeVisible();
    expect(within(publicationDialog).getByText('Публикация')).toBeVisible();
    expect(within(publicationDialog).getByText('Установка')).toBeVisible();
    expect(within(publicationDialog).getByRole('button', { name: 'Опубликовать и подключить' })).toBeEnabled();
  });

  it('lets the owner correct a claimed URL and brief before the first run', async () => {
    const claimed = {
      ...project(),
      source_url: 'http://legacy.example.com',
      brief: 'Старое пожелание',
    };
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (url === '/api/auth/session') return sessionResponse();
      if (url === `/api/projects/${PROJECT_ID}` && init?.method === 'PATCH') {
        return jsonResponse({
          ...claimed,
          source_url: 'https://example.com/services',
          brief: 'Новое пожелание',
        });
      }
      if (url === `/api/projects/${PROJECT_ID}/runs`) return jsonResponse(run(), 202);
      if (url === `/api/projects/${PROJECT_ID}`) return jsonResponse(claimed);
      if (url === `/api/runs/${RUN_ID}`) return jsonResponse(run());
      if (url === `/api/runs/${RUN_ID}/events`) return emptyEventStream();
      throw new Error(`unexpected request: ${url}`);
    }));
    const user = userEvent.setup();

    render(<StudioPage />);

    const url = await screen.findByLabelText('Ссылка на сайт');
    const brief = screen.getByRole('textbox', { name: 'Пожелание к AI-виджету' });
    await waitFor(() => expect(url).toHaveValue('http://legacy.example.com'));
    expect(screen.getByText(
      /Публикация и подключение готового виджета доступны по тарифу/,
    )).toBeVisible();
    expect(screen.queryByText(/Доработка и публикация потребуют тариф/i)).not.toBeInTheDocument();
    expect(url).not.toHaveAttribute('readonly');
    expect(brief).not.toHaveAttribute('readonly');
    await user.clear(url);
    await user.type(url, 'https://EXAMPLE.com:443/services');
    await user.clear(brief);
    await user.type(brief, 'Новое пожелание');
    await user.click(screen.getByRole('button', { name: 'Создать AI-виджет' }));

    await waitFor(() => expect(
      requests.some(({ url: requestUrl }) => requestUrl === `/api/projects/${PROJECT_ID}/runs`),
    ).toBe(true));
    const updateIndex = requests.findIndex(({ init }) => init?.method === 'PATCH');
    const runIndex = requests.findIndex(({ url: requestUrl }) => requestUrl === `/api/projects/${PROJECT_ID}/runs`);
    expect(updateIndex).toBeGreaterThan(-1);
    expect(runIndex).toBeGreaterThan(updateIndex);
    expect(JSON.parse(String(requests[updateIndex].init?.body))).toEqual({
      url: 'https://example.com/services',
      brief: 'Новое пожелание',
    });
    expect(new Headers(requests[updateIndex].init?.headers).get('X-CSRF-Token')).toBe('csrf-for-studio');
  });

  it('starts a paid project refinement and renders version history', async () => {
    const completed = run({
      status: 'completed',
      state: 'completed',
      progress: 100,
      preview: {
        id: 'artifact-version-1',
        revision: 1,
        body_html: '<main>Ready</main>',
        css: '',
        javascript: '',
        quality_status: 'verified',
        source: 'accepted_artifact',
      },
    });
    const refinement = run({
      id: 'run-refinement-2',
      status: 'queued',
      state: 'queued',
      latest_sequence: 0,
      preview: null,
    });
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (url === '/api/auth/session') return sessionResponse();
      if (url === `/api/projects/${PROJECT_ID}`) return jsonResponse(project(completed));
      if (url === `/api/runs/${RUN_ID}`) return jsonResponse(completed);
      if (url === `/api/projects/${PROJECT_ID}/versions`) {
        return jsonResponse({
          active_version_id: 'version-1',
          versions: [{
            id: 'version-1',
            project_id: PROJECT_ID,
            ordinal: 1,
            kind: 'initial',
            change_request: null,
            parent_version_id: null,
            run_id: RUN_ID,
            artifact_id: 'artifact-version-1',
            artifact_revision: 1,
            refinable: true,
            created_at: '2026-07-30T08:00:00Z',
          }],
        });
      }
      if (url === '/api/artifacts/artifact-version-1') {
        return jsonResponse(versionArtifact(
          'artifact-version-1',
          1,
          'Проверенная активная концепция',
        ));
      }
      if (url === `/api/projects/${PROJECT_ID}/versions/version-1/refine`) {
        return jsonResponse(refinement, 202);
      }
      if (url === '/api/runs/run-refinement-2/events') return emptyEventStream();
      if (url === '/api/runs/run-refinement-2') return jsonResponse(refinement);
      if (url === '/api/billing/subscription') return jsonResponse({ subscription: null });
      if (url === '/api/billing/payments/pending') {
        return jsonResponse({ payment: null, checkout_url: null });
      }
      throw new Error(`unexpected request: ${url}`);
    }));
    const user = userEvent.setup();

    render(<StudioPage />);

    expect(screen.queryByRole('region', { name: 'История версий' })).not.toBeInTheDocument();
    await user.click(await screen.findByRole('button', { name: 'Открыть версии' }));
    expect(await screen.findByRole('region', { name: 'История версий' })).toBeVisible();
    expect(screen.getAllByText('example.com').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'Открыть мои виджеты' })).toBeVisible();
    expect(screen.getByText('Текущая версия')).toBeVisible();
    const friendlyContent = document.body.textContent ?? '';
    for (const technicalTerm of ['Сессия', 'Gemini staged', 'Antigravity agent', 'runtime', 'launcher', 'sandbox', 'Ревизия']) {
      expect(friendlyContent).not.toContain(technicalTerm);
    }
    await user.keyboard('{Escape}');
    await user.type(
      screen.getByLabelText('Что изменить в виджете?'),
      'Сделай приветствие короче',
    );
    await user.click(screen.getByRole('button', { name: 'Применить изменение' }));

    await waitFor(() => expect(requests.some(
      ({ url }) => url === `/api/projects/${PROJECT_ID}/versions/version-1/refine`,
    )).toBe(true));
    const request = requests.find(
      ({ url }) => url === `/api/projects/${PROJECT_ID}/versions/version-1/refine`,
    )!;
    expect(JSON.parse(String(request.init?.body))).toEqual({
      change_request: 'Сделай приветствие короче',
      expected_active_version_id: 'version-1',
    });
    expect(new Headers(request.init?.headers).get('X-CSRF-Token')).toBe('csrf-for-studio');
    expect(new Headers(request.init?.headers).get('Idempotency-Key')).toMatch(/^refine-version-1-/);
  });

  it('reloads project versions after a refinement CAS conflict without resubmitting', async () => {
    const completed = run({
      status: 'completed',
      state: 'completed',
      progress: 100,
      preview: {
        id: 'artifact-version-1',
        revision: 1,
        body_html: '<main>Ready</main>',
        css: '',
        javascript: '',
        quality_status: 'verified',
        source: 'accepted_artifact',
      },
    });
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (url === '/api/auth/session') return sessionResponse();
      if (url === `/api/projects/${PROJECT_ID}`) return jsonResponse(project(completed));
      if (url === `/api/runs/${RUN_ID}`) return jsonResponse(completed);
      if (url === `/api/projects/${PROJECT_ID}/versions`) {
        return jsonResponse({
          active_version_id: 'version-1',
          versions: [{
            id: 'version-1', project_id: PROJECT_ID, ordinal: 1, kind: 'initial',
            change_request: null, parent_version_id: null, run_id: RUN_ID,
            artifact_id: 'artifact-version-1', artifact_revision: 1,
            refinable: true, created_at: '2026-07-30T08:00:00Z',
          }],
        });
      }
      if (url === '/api/artifacts/artifact-version-2') {
        return jsonResponse(versionArtifact('artifact-version-2', 2, 'Текущая концепция'));
      }
      if (url === '/api/artifacts/artifact-version-1') {
        return jsonResponse(versionArtifact(
          'artifact-version-1',
          1,
          'Точная историческая концепция',
        ));
      }
      if (url === `/api/projects/${PROJECT_ID}/versions/version-1/refine`) {
        return jsonResponse({
          error: {
            code: 'project_version_conflict',
            message: 'Project version changed',
            retryable: true,
          },
        }, 409);
      }
      if (url === '/api/billing/subscription') return jsonResponse({ subscription: null });
      if (url === '/api/billing/payments/pending') {
        return jsonResponse({ payment: null, checkout_url: null });
      }
      throw new Error(`unexpected request: ${url}`);
    }));
    const user = userEvent.setup();
    render(<StudioPage />);
    await user.type(
      await screen.findByLabelText('Что изменить в виджете?'),
      'Сделай приветствие короче',
    );
    await user.click(screen.getByRole('button', { name: 'Применить изменение' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Версия проекта изменилась в другой вкладке. Данные обновлены — проверьте их и повторите доработку.',
    );
    expect(requests.filter(
      ({ url }) => url === `/api/projects/${PROJECT_ID}/versions/version-1/refine`,
    )).toHaveLength(1);
    expect(requests.filter(
      ({ url }) => url === `/api/projects/${PROJECT_ID}/versions`,
    ).length).toBeGreaterThanOrEqual(2);
  });

  it('keeps the active durable preview and publication target after a failed refinement', async () => {
    const failedRefinement = run({
      id: 'run-refinement-failed',
      status: 'failed',
      state: 'failed',
      progress: 84,
      error_code: 'visual_quality_failed',
      error_message: 'Visual audit failed',
      preview: {
        id: 'artifact-failed-draft',
        revision: 4,
        body_html: '<main>Failed draft</main>',
        css: '',
        javascript: '',
        quality_status: 'restorable_draft',
        source: 'restorable_draft',
      },
    });
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/auth/session') return sessionResponse();
      if (url === `/api/projects/${PROJECT_ID}`) {
        return jsonResponse({
          ...project(failedRefinement),
          active_version_id: 'version-1',
        });
      }
      if (url === '/api/runs/run-refinement-failed') return jsonResponse(failedRefinement);
      if (url === `/api/projects/${PROJECT_ID}/versions`) {
        return jsonResponse({
          active_version_id: 'version-1',
          versions: [{
            id: 'version-1', project_id: PROJECT_ID, ordinal: 1, kind: 'initial',
            change_request: null, parent_version_id: null,
            run_id: 'run-version-1', artifact_id: 'artifact-version-1',
            artifact_revision: 1, refinable: true,
            created_at: '2026-07-30T08:00:00Z',
          }],
        });
      }
      if (url === '/api/artifacts/artifact-version-1') {
        return jsonResponse(versionArtifact(
          'artifact-version-1',
          1,
          'Проверенная активная концепция',
        ));
      }
      if (url === '/api/billing/subscription') return jsonResponse({ subscription: null });
      if (url === '/api/billing/payments/pending') {
        return jsonResponse({ payment: null, checkout_url: null });
      }
      throw new Error(`unexpected request: ${url}`);
    }));

    const user = userEvent.setup();
    render(<StudioPage />);

    const preview = await screen.findByTitle('Предпросмотр консультанта Kaigo');
    expect(preview).toHaveAttribute(
      'src',
      expect.stringContaining('/api/runs/run-version-1/preview/document?revision=1'),
    );
    await user.click(screen.getByRole('button', { name: 'Открыть публикацию' }));
    expect(await screen.findByRole('button', { name: 'Опубликовать и подключить' })).toBeVisible();
  });

  it('publishes the selected historical version without restoring it first', async () => {
    const current = run({
      status: 'completed',
      state: 'completed',
      progress: 100,
      preview: {
        id: 'artifact-version-2',
        revision: 2,
        body_html: '<main>Version 2</main>',
        css: '',
        javascript: '',
        quality_status: 'verified',
        source: 'accepted_artifact',
      },
    });
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (url === '/api/auth/session') return sessionResponse();
      if (url === `/api/projects/${PROJECT_ID}`) {
        return jsonResponse({ ...project(current), active_version_id: 'version-2' });
      }
      if (url === `/api/runs/${RUN_ID}`) return jsonResponse(current);
      if (url === `/api/projects/${PROJECT_ID}/versions`) {
        return jsonResponse({
          active_version_id: 'version-2',
          versions: [
            {
              id: 'version-2', project_id: PROJECT_ID, ordinal: 2, kind: 'refinement',
              change_request: 'Добавь ответы', parent_version_id: 'version-1',
              run_id: RUN_ID, artifact_id: 'artifact-version-2',
              artifact_revision: 2, refinable: true,
              created_at: '2026-07-30T09:00:00Z',
            },
            {
              id: 'version-1', project_id: PROJECT_ID, ordinal: 1, kind: 'initial',
              change_request: null, parent_version_id: null,
              run_id: 'run-version-1', artifact_id: 'artifact-version-1',
              artifact_revision: 1, refinable: true,
              created_at: '2026-07-30T08:00:00Z',
            },
          ],
        });
      }
      if (url === '/api/artifacts/artifact-version-2') {
        return jsonResponse(versionArtifact('artifact-version-2', 2, 'Текущая концепция'));
      }
      if (url === '/api/artifacts/artifact-version-1') {
        return jsonResponse(versionArtifact(
          'artifact-version-1',
          1,
          'Точная историческая концепция',
        ));
      }
      if (url === '/api/billing/subscription') {
        return jsonResponse({
          subscription: {
            id: 'subscription-123',
            plan_code: 'starter_monthly',
            status: 'active',
            current_period_start: '2026-07-28T12:00:00Z',
            current_period_end: '2026-08-28T12:00:00Z',
            auto_renew: true,
            next_renewal_at: '2026-08-28T12:00:00Z',
            generation_tokens_remaining: 750_000,
          },
        });
      }
      if (url === `/api/projects/${PROJECT_ID}/publish` && init?.method === 'POST') {
        return jsonResponse({
          publication_id: 'publication-123',
          release_id: 'release-1',
          artifact_id: 'artifact-version-1',
          project_version_id: 'version-1',
          stable_key: 'stable-widget',
          revision: 1,
          allowed_domains: ['https://example.com'],
          checksum: 'checksum-1',
          embed_url: 'https://widgets.kaigo.space/embed/stable-widget.js',
          runtime_url: 'https://widgets.kaigo.space/runtime/stable-widget',
        });
      }
      if (url === `/api/projects/${PROJECT_ID}/publication`) {
        return jsonResponse({ publication: null });
      }
      throw new Error(`unexpected request: ${url}`);
    }));
    const user = userEvent.setup();

    render(<StudioPage />);

    await user.click(await screen.findByRole('button', { name: 'Открыть тариф и лимиты' }));
    const accountDialog = screen.getByRole('dialog', { name: 'Тариф и лимиты' });
    expect(within(accountDialog).getByText('Starter')).toBeVisible();
    expect(within(accountDialog).getByText(/750\s000/)).toBeVisible();
    expect(within(accountDialog).getByText('Автопродление включено')).toBeVisible();
    await user.keyboard('{Escape}');

    await user.click(await screen.findByRole('button', { name: 'Открыть версии' }));
    await user.click(await screen.findByRole('button', { name: 'Просмотреть версию 1' }));
    const selectedPreview = screen.getByRole('region', { name: 'Предпросмотр виджета' });
    expect(within(selectedPreview).getByText('Версия 1')).toBeVisible();
    expect(within(selectedPreview).getByText('Проверено')).toBeVisible();
    expect(screen.queryByText('Виджет готов к просмотру. Проверьте его на компьютере и телефоне.')).not.toBeInTheDocument();
    expect(screen.queryByText('Точная историческая концепция')).not.toBeInTheDocument();
    expect(requests.some(({ url }) => url === '/api/artifacts/artifact-version-1')).toBe(true);
    await user.click(screen.getByRole('button', { name: 'Открыть публикацию' }));
    const domains = await screen.findByLabelText('На каких сайтах разрешить виджет');
    await user.type(domains, 'https://example.com');
    const publishButton = await screen.findByRole('button', { name: 'Опубликовать виджет' });
    await waitFor(() => expect(publishButton).toBeEnabled());
    await user.click(publishButton);

    await waitFor(() => expect(requests.some(
      ({ url, init }) => url === `/api/projects/${PROJECT_ID}/publish` && init?.method === 'POST',
    )).toBe(true));
    const publish = requests.find(
      ({ url, init }) => url === `/api/projects/${PROJECT_ID}/publish` && init?.method === 'POST',
    )!;
    expect(JSON.parse(String(publish.init?.body))).toEqual({
      project_version_id: 'version-1',
      expected_active_release_id: null,
      allowed_domains: ['https://example.com'],
    });
    expect(requests.some(({ url }) => url.endsWith('/versions/version-1/restore'))).toBe(false);
  });

  it('restores an earlier verified project version', async () => {
    const current = run({
      status: 'completed',
      state: 'completed',
      progress: 100,
      preview: {
        id: 'artifact-version-2',
        revision: 2,
        body_html: '<main>Version 2</main>',
        css: '',
        javascript: '',
        quality_status: 'verified',
        source: 'accepted_artifact',
      },
    });
    const oldRun = run({
      id: 'run-version-1',
      status: 'completed',
      state: 'completed',
      progress: 100,
      preview: {
        id: 'artifact-version-1',
        revision: 1,
        body_html: '<main>Version 1</main>',
        css: '',
        javascript: '',
        quality_status: 'verified',
        source: 'accepted_artifact',
      },
    });
    let restored = false;
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, init });
      if (url === '/api/auth/session') return sessionResponse();
      if (url === `/api/projects/${PROJECT_ID}`) {
        return jsonResponse({
          ...project(restored ? oldRun : current),
          active_version_id: restored ? 'version-3' : 'version-2',
        });
      }
      if (url === `/api/runs/${RUN_ID}`) return jsonResponse(current);
      if (url === '/api/runs/run-version-1') return jsonResponse(oldRun);
      if (url === `/api/projects/${PROJECT_ID}/versions`) {
        const base = [
          {
            id: 'version-2', project_id: PROJECT_ID, ordinal: 2, kind: 'refinement',
            change_request: 'Добавь ответы', parent_version_id: 'version-1',
            run_id: RUN_ID, artifact_id: 'artifact-version-2',
            artifact_revision: 2, refinable: true,
            created_at: '2026-07-30T09:00:00Z',
          },
          {
            id: 'version-1', project_id: PROJECT_ID, ordinal: 1, kind: 'initial',
            change_request: null, parent_version_id: null,
            run_id: 'run-version-1', artifact_id: 'artifact-version-1',
            artifact_revision: 1, refinable: true,
            created_at: '2026-07-30T08:00:00Z',
          },
        ];
        return jsonResponse({
          active_version_id: restored ? 'version-3' : 'version-2',
          versions: restored
            ? [{
                id: 'version-3', project_id: PROJECT_ID, ordinal: 3, kind: 'restore',
                change_request: null, parent_version_id: 'version-1',
                run_id: 'run-version-1', artifact_id: 'artifact-version-1',
                artifact_revision: 1, refinable: true,
                created_at: '2026-07-30T10:00:00Z',
              }, ...base]
            : base,
        });
      }
      if (url === '/api/artifacts/artifact-version-2') {
        return jsonResponse(versionArtifact('artifact-version-2', 2, 'Текущая концепция'));
      }
      if (url === '/api/artifacts/artifact-version-1') {
        return jsonResponse(versionArtifact('artifact-version-1', 1, 'Восстановленная концепция'));
      }
      if (url === `/api/projects/${PROJECT_ID}/versions/version-1/restore`) {
        restored = true;
        return jsonResponse({ version: { id: 'version-3' } }, 201);
      }
      if (url === '/api/billing/subscription') return jsonResponse({ subscription: null });
      if (url === '/api/billing/payments/pending') {
        return jsonResponse({ payment: null, checkout_url: null });
      }
      throw new Error(`unexpected request: ${url}`);
    }));
    const user = userEvent.setup();

    render(<StudioPage />);

    await user.click(await screen.findByRole('button', { name: 'Открыть версии' }));
    await user.click(await screen.findByRole('button', { name: 'Просмотреть версию 1' }));
    expect(screen.getByTitle('Предпросмотр консультанта Kaigo')).toHaveAttribute(
      'src',
      expect.stringContaining('/api/runs/run-version-1/preview/document?revision=1'),
    );
    await user.click(screen.getByRole('button', { name: 'Открыть версии' }));
    await user.click(await screen.findByRole('button', { name: 'Восстановить версию 1' }));

    await waitFor(() => expect(restored).toBe(true));
    await waitFor(() => expect(
      screen.getAllByText('Версия восстановлена').length,
    ).toBeGreaterThan(0));
    const request = requests.find(
      ({ url }) => url === `/api/projects/${PROJECT_ID}/versions/version-1/restore`,
    )!;
    expect(new Headers(request.init?.headers).get('Idempotency-Key')).toMatch(
      /^restore-version-2-/,
    );
    expect(JSON.parse(String(request.init?.body))).toEqual({
      expected_active_version_id: 'version-2',
    });
  });
});
