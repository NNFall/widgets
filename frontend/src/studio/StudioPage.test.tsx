import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { StudioPage } from './StudioPage';
import type { BuilderEvent, BuilderRunSnapshot } from './types';

const ACTIVE_RUN_STORAGE_KEY = 'kaigo.builder.activeRun.v1';

class FakeEventSource {
  static instances: FakeEventSource[] = [];

  readonly url: string;
  closed = false;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string | URL) {
    this.url = String(url);
    FakeEventSource.instances.push(this);
  }

  close() {
    this.closed = true;
  }

  emit(payload: BuilderEvent) {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(payload) }));
  }

  fail() {
    this.onerror?.();
  }
}

const artifact = {
  schema_version: '1',
  revision: 4,
  stage: 'motion_polish' as const,
  art_direction: 'Тёплый редакционный интерфейс в стиле исходного сайта',
  body_html: '<div data-region="root"></div>',
  css: '',
  javascript: '',
  theme_tokens: {},
  suggested_actions: [],
  change_summary: 'Смягчена анимация открытия',
  layout_contract: {},
};

function snapshot(overrides: Partial<BuilderRunSnapshot> = {}): BuilderRunSnapshot {
  return {
    run_id: 'run-123',
    request: {
      engine: 'direct',
      brief: 'Спокойный консультант',
      reference_context: '',
      source_url: 'https://example.com/',
      locale: 'ru',
      creativity: 0.9,
      viewport_targets: ['desktop', 'mobile'],
      max_repairs: 3,
      contract_id: 'chat-v1',
      creative_profile: 'balanced',
      visual_repair_limit: 8,
    },
    status: 'running',
    created_at: '2026-07-27T00:00:00+00:00',
    updated_at: '2026-07-27T00:00:03+00:00',
    latest_sequence: 1,
    artifact: null,
    draft_artifact: null,
    quality_status: 'pending',
    usage: {
      prompt_tokens: 1200,
      output_tokens: 400,
      thinking_tokens: 220,
      total_tokens: 1820,
    },
    elapsed_seconds: 3.2,
    error_code: null,
    cancel_requested: false,
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve;
    reject = promiseReject;
  });
  return { promise, resolve, reject };
}

function event(overrides: Partial<BuilderEvent> = {}): BuilderEvent {
  return {
    run_id: 'run-123',
    sequence: 1,
    timestamp: '2026-07-27T00:00:00+00:00',
    type: 'run.created',
    stage: null,
    status: 'created',
    message: 'Запуск создан',
    revision: null,
    usage: { prompt_tokens: 0, output_tokens: 0, thinking_tokens: 0, total_tokens: 0 },
    issues: [],
    changes: [],
    error_code: null,
    ...overrides,
  };
}

beforeEach(() => {
  localStorage.clear();
  FakeEventSource.instances = [];
  vi.stubGlobal('EventSource', FakeEventSource);
  window.history.replaceState({}, '', '/studio');
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('StudioPage', () => {
  it('creates a Builder run, stores it and follows Russian timeline events', async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(snapshot(), 202)));
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();

    render(<StudioPage />);
    fireEvent.change(screen.getByLabelText('Ссылка на сайт'), {
      target: { value: 'https://example.com' },
    });
    fireEvent.change(screen.getByLabelText('Пожелание к AI-сотруднику'), {
      target: { value: 'Отвечай кратко и по делу' },
    });
    await user.click(screen.getByRole('button', { name: 'Создать AI-виджет' }));

    expect((await screen.findAllByText('Запуск создан')).some((item) => item.textContent === 'Запуск создан')).toBe(true);
    expect(localStorage.getItem(ACTIVE_RUN_STORAGE_KEY)).toBe('run-123');
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.instances[0].url).toBe('http://localhost:3000/builder/api/runs/run-123/events');
    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:3000/builder/api/runs',
      expect.objectContaining({
        method: 'POST',
        body: expect.stringContaining('"source_url":"https://example.com"'),
      }),
    );

    act(() => {
      FakeEventSource.instances[0].emit(event({
        sequence: 2,
        type: 'reference.started',
        stage: 'art_direction',
        status: 'running',
        message: 'Изучаем структуру и визуальный язык сайта',
      }));
    });
    expect((await screen.findAllByText('Изучаем структуру и визуальный язык сайта')).length).toBeGreaterThan(0);
    expect(screen.getByText('Анализ сайта начат')).toBeVisible();
    await waitFor(() => expect(document.querySelector('.studio-header__session strong')).toHaveTextContent('Изучаем структуру и визуальный язык сайта'));
  });

  it('resumes the active run, restores fields and shows the latest preview revision', async () => {
    localStorage.setItem(ACTIVE_RUN_STORAGE_KEY, 'run-123');
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(snapshot({
      status: 'completed',
      artifact,
      quality_status: 'verified',
      elapsed_seconds: 47.6,
    })))));

    render(<StudioPage />);

    expect(await screen.findByDisplayValue('https://example.com/')).toBeVisible();
    expect(screen.getByDisplayValue('Спокойный консультант')).toBeVisible();
    expect(screen.getByText('1 820')).toBeVisible();
    expect(screen.getByText('47,6 с')).toBeVisible();
    const preview = screen.getByTitle('Предпросмотр AI-сотрудника Kaigo');
    expect(preview).toHaveAttribute('sandbox', 'allow-scripts');
    expect(preview.getAttribute('src')).toMatch(
      /^http:\/\/localhost:3000\/builder\/api\/runs\/run-123\/preview\?revision=4&channel=[A-Za-z0-9_-]{22,96}$/,
    );
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.instances[0].closed).toBe(false);
  });

  it('supports cancel, retry and refine without inventing a publish endpoint', async () => {
    localStorage.setItem(ACTIVE_RUN_STORAGE_KEY, 'run-123');
    let current = snapshot();
    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/cancel')) return jsonResponse({ run_id: 'run-123', cancel_requested: true }, 202);
      if (url.endsWith('/retry')) {
        current = snapshot({ run_id: 'run-retry' });
        return jsonResponse(current, 202);
      }
      if (url.endsWith('/refine')) {
        current = snapshot({ run_id: 'run-refined', status: 'running', artifact });
        return jsonResponse(current, 202);
      }
      if (init?.method === 'GET' || !init?.method) return jsonResponse(current);
      return jsonResponse(current, 202);
    });
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    render(<StudioPage />);

    await screen.findByDisplayValue('https://example.com/');
    await user.click(screen.getByRole('button', { name: 'Отменить генерацию' }));
    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:3000/builder/api/runs/run-123/cancel',
      expect.objectContaining({ method: 'POST' }),
    );

    cleanup();
    localStorage.setItem(ACTIVE_RUN_STORAGE_KEY, 'run-123');
    current = snapshot({ status: 'failed', error_code: 'generation_timeout' });
    render(<StudioPage />);
    await user.click(await screen.findByRole('button', { name: 'Повторить запуск' }));
    expect(localStorage.getItem(ACTIVE_RUN_STORAGE_KEY)).toBe('run-retry');

    cleanup();
    localStorage.setItem(ACTIVE_RUN_STORAGE_KEY, 'run-123');
    current = snapshot({ status: 'completed', artifact, quality_status: 'verified' });
    render(<StudioPage />);
    const refinement = await screen.findByLabelText('Что изменить в виджете?');
    await user.type(refinement, 'Сделай приветствие короче');
    await user.click(screen.getByRole('button', { name: 'Применить изменение' }));
    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:3000/builder/api/runs/run-123/refine',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ message: 'Сделай приветствие короче' }),
      }),
    );
    expect(localStorage.getItem(ACTIVE_RUN_STORAGE_KEY)).toBe('run-refined');

    const publish = screen.getByRole('button', { name: /Опубликовать.*Скоро/i });
    expect(publish).toBeDisabled();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('publish'))).toBe(false);
  });

  it('switches the live preview between desktop and mobile canvases', async () => {
    localStorage.setItem(ACTIVE_RUN_STORAGE_KEY, 'run-123');
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(snapshot({
      status: 'completed',
      artifact,
      quality_status: 'verified',
    })))));
    const user = userEvent.setup();

    render(<StudioPage />);
    const canvas = await screen.findByTestId('studio-preview-canvas');
    expect(canvas).toHaveAttribute('data-viewport', 'desktop');
    expect(screen.getByRole('button', { name: 'Desktop' })).toHaveAttribute('aria-pressed', 'true');

    await user.click(screen.getByRole('button', { name: 'Mobile' }));
    expect(canvas).toHaveAttribute('data-viewport', 'mobile');
    expect(screen.getByRole('button', { name: 'Mobile' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('shows a human terminal error and keeps raw provider details disclosed separately', async () => {
    localStorage.setItem(ACTIVE_RUN_STORAGE_KEY, 'run-123');
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(snapshot({
      status: 'failed',
      error_code: 'visual_quality_failed',
    })))));

    render(<StudioPage />);
    await screen.findByRole('alert');
    act(() => {
      FakeEventSource.instances[0].emit(event({
        sequence: 9,
        type: 'run.failed',
        stage: 'motion_polish',
        status: 'failed',
        message: 'Gemini returned an invalid grounded reference',
        error_code: 'visual_quality_failed',
      }));
    });

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Финальная визуальная проверка не пройдена');
    const timeline = screen.getByRole('region', { name: 'Диалог с генератором' });
    expect(within(timeline).getByText('Финальная визуальная проверка не пройдена.')).toBeVisible();
    expect(within(timeline).queryByText('Gemini returned an invalid grounded reference')).not.toBeInTheDocument();
    const details = within(alert).getByText('Детали');
    expect(details.closest('details')).not.toHaveAttribute('open');
    expect(within(alert).getByText('Gemini returned an invalid grounded reference')).toBeInTheDocument();
    expect(screen.getAllByText('Gemini returned an invalid grounded reference')).toHaveLength(1);
  });

  it('ignores a stale slower snapshot after a newer terminal revision was accepted', async () => {
    localStorage.setItem(ACTIVE_RUN_STORAGE_KEY, 'run-123');
    const olderResponse = deferred<Response>();
    const newerResponse = deferred<Response>();
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => olderResponse.promise)
      .mockImplementationOnce(() => newerResponse.promise);
    vi.stubGlobal('fetch', fetchMock);

    render(<StudioPage />);
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    act(() => {
      FakeEventSource.instances[0].emit(event({
        sequence: 10,
        type: 'artifact.committed',
        stage: 'motion_polish',
        status: 'completed',
        message: 'Ревизия 5 готова',
        revision: 5,
      }));
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    await act(async () => {
      newerResponse.resolve(jsonResponse(snapshot({
        status: 'completed',
        latest_sequence: 10,
        updated_at: '2026-07-27T00:00:10+00:00',
        artifact: { ...artifact, revision: 5 },
        quality_status: 'verified',
      })));
      await newerResponse.promise;
    });
    expect(await screen.findByText('5')).toBeVisible();
    expect(document.querySelector('.studio-header__session strong')).toHaveTextContent('Готово — виджет проверен');

    await act(async () => {
      olderResponse.resolve(jsonResponse(snapshot({
        status: 'running',
        latest_sequence: 2,
        updated_at: '2026-07-27T00:00:02+00:00',
        artifact: { ...artifact, revision: 2 },
        quality_status: 'pending',
      })));
      await olderResponse.promise;
    });
    expect(screen.getByText('5')).toBeVisible();
    expect(screen.queryByText('2')).not.toBeInTheDocument();
    expect(document.querySelector('.studio-header__session strong')).toHaveTextContent('Готово — виджет проверен');
  });

  it('falls back from EventSource to snapshot polling and closes resources on unmount', async () => {
    vi.useFakeTimers();
    localStorage.setItem(ACTIVE_RUN_STORAGE_KEY, 'run-123');
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(snapshot())));
    vi.stubGlobal('fetch', fetchMock);

    const { unmount } = render(<StudioPage />);
    await act(async () => Promise.resolve());
    expect(FakeEventSource.instances).toHaveLength(1);

    act(() => FakeEventSource.instances[0].fail());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_100);
    });
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/api/runs/run-123')).length).toBeGreaterThanOrEqual(2);

    unmount();
    expect(FakeEventSource.instances[0].closed).toBe(true);
    const countAtUnmount = fetchMock.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(countAtUnmount);
    vi.useRealTimers();
  });

  it('bridges only integrity-checked chat messages from the active preview iframe', async () => {
    localStorage.setItem(ACTIVE_RUN_STORAGE_KEY, 'run-123');
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      if (String(input).endsWith('/chat')) {
        return jsonResponse({ request_id: 'request-valid-123', reply: 'Проверенный ответ' });
      }
      return jsonResponse(snapshot({ status: 'completed', artifact, quality_status: 'verified' }));
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<StudioPage />);
    const iframe = await screen.findByTitle('Предпросмотр AI-сотрудника Kaigo') as HTMLIFrameElement;
    const src = new URL(iframe.getAttribute('src')!);
    const channel = src.searchParams.get('channel');
    const postMessage = vi.spyOn(iframe.contentWindow!, 'postMessage');

    fireEvent(window, new MessageEvent('message', {
      source: iframe.contentWindow,
      data: {
        source: 'kaigo-builder-preview',
        version: 2,
        channel_id: 'wrong-channel-000000000',
        type: 'chat.request',
        request_id: 'request-valid-123',
        revision: 4,
        text: 'Что вы умеете?',
      },
    }));
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/chat'))).toBe(false);

    fireEvent(window, new MessageEvent('message', {
      source: iframe.contentWindow,
      data: {
        source: 'kaigo-builder-preview',
        version: 2,
        channel_id: channel,
        type: 'chat.request',
        request_id: 'request-valid-123',
        revision: 4,
        text: 'Что вы умеете?',
      },
    }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/chat'))).toBe(true));
    expect(postMessage).toHaveBeenCalledWith(expect.objectContaining({
      source: 'kaigo-builder-parent',
      version: 2,
      channel_id: channel,
      revision: 4,
      type: 'chat.response',
      request_id: 'request-valid-123',
      text: 'Проверенный ответ',
    }), '*');
  });
});
