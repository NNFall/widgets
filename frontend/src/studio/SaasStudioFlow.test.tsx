import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { StudioPage } from './StudioPage';

const PROJECT_ID = '5d34bcab-cfd3-4ca2-8398-eb59f34aab92';
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
      if (url === '/api/projects') return jsonResponse(project(), 201);
      if (url === `/api/projects/${PROJECT_ID}`) return jsonResponse(project());
      throw new Error(`unexpected request: ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();

    render(<StudioPage />);

    expect(await screen.findByRole('heading', { name: 'Новый проект в Kaigo Studio' })).toBeVisible();
    expect(screen.getByLabelText('Ссылка на сайт')).toHaveValue('https://fresh.example.com');
    await user.type(
      screen.getByRole('textbox', { name: 'Пожелание к AI-виджету' }),
      'Спокойный консультант',
    );
    await user.click(screen.getByRole('button', { name: 'Создать проект' }));

    await waitFor(() => expect(requests.some(({ url }) => url === '/api/projects')).toBe(true));
    const create = requests.find(({ url }) => url === '/api/projects')!;
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

  it('restores an authenticated queued run from the project API after reload', async () => {
    const queued = run();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/auth/session') return sessionResponse();
      if (url === `/api/projects/${PROJECT_ID}`) return jsonResponse(project(queued));
      if (url === `/api/runs/${RUN_ID}`) return jsonResponse(queued);
      if (url === `/api/runs/${RUN_ID}/events`) return emptyEventStream();
      throw new Error(`unexpected request: ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<StudioPage />);

    expect((await screen.findAllByText('Запуск в очереди'))[0]).toBeVisible();
    expect(await screen.findByDisplayValue('https://example.com')).toHaveAttribute('readonly');
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
    expect(await screen.findByText('Запуск поставлен в очередь')).toBeVisible();
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
    });
    const replacement = run({
      id: '2d44e96d-6e52-43b7-ada4-dc88d625a9e7',
      status: 'queued',
      state: 'queued',
      error_code: null,
      error_message: null,
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

    const frame = await screen.findByTitle('Предпросмотр AI-сотрудника Kaigo');
    const previewUrl = new URL(frame.getAttribute('src')!);
    expect(previewUrl.pathname).toBe(`/api/runs/${RUN_ID}/preview/document`);
    expect(previewUrl.searchParams.get('revision')).toBe('2');
    expect(previewUrl.searchParams.get('channel')).toMatch(/^[a-f0-9]{36}$/);
    expect(frame).not.toHaveAttribute('srcdoc');
    expect(frame).toHaveAttribute('sandbox', 'allow-scripts');
    expect(screen.queryByText('Бесплатный результат готов')).not.toBeInTheDocument();
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
    const frame = await screen.findByTitle('Предпросмотр AI-сотрудника Kaigo') as HTMLIFrameElement;
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

    expect((await screen.findAllByText('Собираем основу'))[0]).toBeVisible();
    expect(screen.queryByText('Дубликат')).not.toBeInTheDocument();
    expect(screen.queryByText('Старое событие')).not.toBeInTheDocument();
    expect(document.querySelectorAll('.studio-event')).toHaveLength(2);
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

    render(<StudioPage />);

    const metrics = await screen.findByLabelText('Метрики запуска');
    expect(within(metrics).getByText('60')).toBeVisible();
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
      if (url === '/api/billing/subscription') return jsonResponse({ subscription: null });
      if (url === '/api/billing/payments/pending') {
        return jsonResponse({ payment: null, checkout_url: null });
      }
      throw new Error(`unexpected request: ${url}`);
    }));

    render(<StudioPage />);

    expect((await screen.findAllByText('Готово')).length).toBeGreaterThanOrEqual(1);
    expect(await screen.findByText('Бесплатный результат готов')).toBeVisible();
    expect(await screen.findByRole('button', { name: 'Опубликовать и подключить' })).toBeEnabled();
    expect(screen.queryByLabelText('Что изменить в виджете?')).not.toBeInTheDocument();
    expect(screen.getByText(
      'Чат в предпросмотре проверяет ответы виджета, но не изменяет его.',
    )).toBeVisible();
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
});
