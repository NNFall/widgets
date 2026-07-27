import { test as base, expect, type Page, type Route } from '@playwright/test';

type RunStatus = 'created' | 'running' | 'completed' | 'failed' | 'cancelled';

interface FakeBuilderRequest {
  engine: 'direct' | 'antigravity';
  brief: string;
  reference_context: string;
  source_url: string;
  locale: string;
  creativity: number;
  viewport_targets: Array<'desktop' | 'mobile'>;
  max_repairs: number;
  contract_id: string;
  creative_profile: 'balanced' | 'product_chat' | 'brand_motion' | 'ai_character';
  visual_repair_limit: number;
}

interface FakeBuilderSnapshot {
  run_id: string;
  request: FakeBuilderRequest;
  status: RunStatus;
  created_at: string;
  updated_at: string;
  latest_sequence: number;
  artifact: FakeArtifact | null;
  draft_artifact: FakeArtifact | null;
  quality_status: string;
  usage: FakeUsage;
  elapsed_seconds: number;
  error_code: string | null;
  cancel_requested: boolean;
}

interface FakeArtifact {
  schema_version: string;
  revision: number;
  stage: 'motion_polish';
  art_direction: string;
  body_html: string;
  css: string;
  javascript: string;
  theme_tokens: Record<string, string>;
  suggested_actions: string[];
  change_summary: string;
  layout_contract: Record<string, string>;
}

interface FakeUsage {
  prompt_tokens: number;
  output_tokens: number;
  thinking_tokens: number;
  total_tokens: number;
}

interface FakeEvent {
  run_id: string;
  sequence: number;
  timestamp: string;
  type: string;
  stage: 'art_direction' | 'motion_polish' | null;
  status: string;
  message: string;
  revision: number | null;
  usage: FakeUsage;
  issues: Array<{ code: string; field: string; message: string }>;
  changes: string[];
  error_code: string | null;
}

interface BuilderRequestLog {
  method: string;
  pathname: string;
  body: unknown;
}

export interface FakeBuilderApi {
  requests: BuilderRequestLog[];
  seedRun: (runId: string, overrides?: Partial<FakeBuilderSnapshot>) => FakeBuilderSnapshot;
}

const NOW = '2026-07-27T12:00:00.000Z';
const usage: FakeUsage = {
  prompt_tokens: 8_400,
  output_tokens: 2_100,
  thinking_tokens: 1_845,
  total_tokens: 12_345,
};

function artifact(revision = 4): FakeArtifact {
  return {
    schema_version: '2',
    revision,
    stage: 'motion_polish',
    art_direction: 'Тёплый, спокойный AI-консультант в фирменной палитре сайта.',
    body_html: '<main>Fake preview</main>',
    css: 'body { margin: 0; }',
    javascript: '',
    theme_tokens: { accent: '#fe6936', ink: '#071b2f' },
    suggested_actions: ['Узнать об услугах', 'Оставить заявку'],
    change_summary: 'Добавлены понятный диалог, мобильная компоновка и мягкая анимация.',
    layout_contract: { width: 'compact', placement: 'bottom-right' },
  };
}

function request(sourceUrl = 'https://example.com'): FakeBuilderRequest {
  return {
    engine: 'direct',
    brief: 'Уверенный консультант, который говорит простым языком.',
    reference_context: 'E2E fixture',
    source_url: sourceUrl,
    locale: 'ru',
    creativity: 0.9,
    viewport_targets: ['desktop', 'mobile'],
    max_repairs: 3,
    contract_id: 'product-chat-v1',
    creative_profile: 'product_chat',
    visual_repair_limit: 3,
  };
}

function snapshot(
  runId: string,
  overrides: Partial<FakeBuilderSnapshot> = {},
): FakeBuilderSnapshot {
  return {
    run_id: runId,
    request: request(),
    status: 'completed',
    created_at: NOW,
    updated_at: NOW,
    latest_sequence: 3,
    artifact: artifact(),
    draft_artifact: null,
    quality_status: 'verified',
    usage,
    elapsed_seconds: 24.8,
    error_code: null,
    cancel_requested: false,
    ...overrides,
  };
}

function events(runId: string): FakeEvent[] {
  return [
    {
      run_id: runId,
      sequence: 1,
      timestamp: NOW,
      type: 'run.created',
      stage: null,
      status: 'completed',
      message: 'Запуск создан',
      revision: null,
      usage,
      issues: [],
      changes: [],
      error_code: null,
    },
    {
      run_id: runId,
      sequence: 2,
      timestamp: NOW,
      type: 'visual_audit.passed',
      stage: 'motion_polish',
      status: 'completed',
      message: 'Визуальная проверка пройдена',
      revision: 4,
      usage,
      issues: [],
      changes: ['css', 'javascript'],
      error_code: null,
    },
    {
      run_id: runId,
      sequence: 3,
      timestamp: NOW,
      type: 'run.completed',
      stage: null,
      status: 'completed',
      message: 'Виджет готов к проверке',
      revision: 4,
      usage,
      issues: [],
      changes: [],
      error_code: null,
    },
  ];
}

const eventMatrix = Object.fromEntries(
  ['run-created', 'run-resume', 'run-mobile', 'run-a11y', 'run-reduced']
    .map((runId) => [runId, events(runId)]),
);

async function installEventSource(page: Page) {
  await page.addInitScript(({ matrix }) => {
    class DeterministicEventSource {
      static readonly CONNECTING = 0;
      static readonly OPEN = 1;
      static readonly CLOSED = 2;

      readonly CONNECTING = 0;
      readonly OPEN = 1;
      readonly CLOSED = 2;
      readonly url: string;
      readonly withCredentials = false;
      readyState = DeterministicEventSource.CONNECTING;
      onopen: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      private closed = false;
      private listeners = new Map<string, Set<EventListenerOrEventListenerObject>>();

      constructor(url: string | URL) {
        this.url = String(url);
        const match = this.url.match(/\/runs\/([^/]+)\/events/);
        const runId = match ? decodeURIComponent(match[1]) : '';
        const runEvents = matrix[runId] ?? [];
        window.setTimeout(() => {
          if (this.closed) return;
          this.readyState = DeterministicEventSource.OPEN;
          this.dispatchEvent(new Event('open'));
          runEvents.forEach((item, index) => {
            window.setTimeout(() => {
              if (this.closed) return;
              this.dispatchEvent(new MessageEvent('message', {
                data: JSON.stringify(item),
              }));
            }, index * 25);
          });
        }, 40);
      }

      close() {
        this.closed = true;
        this.readyState = DeterministicEventSource.CLOSED;
      }

      addEventListener(type: string, listener: EventListenerOrEventListenerObject | null) {
        if (!listener) return;
        const bucket = this.listeners.get(type) ?? new Set<EventListenerOrEventListenerObject>();
        bucket.add(listener);
        this.listeners.set(type, bucket);
      }

      removeEventListener(type: string, listener: EventListenerOrEventListenerObject | null) {
        if (listener) this.listeners.get(type)?.delete(listener);
      }

      dispatchEvent(event: Event) {
        if (event.type === 'open') this.onopen?.(event);
        if (event.type === 'message') this.onmessage?.(event as MessageEvent);
        if (event.type === 'error') this.onerror?.(event);
        for (const listener of this.listeners.get(event.type) ?? []) {
          if (typeof listener === 'function') listener.call(this, event);
          else listener.handleEvent(event);
        }
        return true;
      }
    }

    Object.defineProperty(window, 'EventSource', {
      configurable: true,
      value: DeterministicEventSource,
    });
  }, { matrix: eventMatrix });
}

function parseBody(route: Route) {
  const raw = route.request().postData();
  if (!raw) return null;
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    return raw;
  }
}

function previewHtml() {
  return `<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <style>
      * { box-sizing: border-box; }
      body { min-height: 100vh; margin: 0; padding: 28px; display: grid; place-items: end; color: #071b2f; background: linear-gradient(145deg, #f8f3ed, #e7efe9); font: 16px/1.45 Arial, sans-serif; }
      main { width: min(360px, 100%); padding: 22px; border: 1px solid #b8c8bf; border-radius: 22px; background: #fff; box-shadow: 0 22px 55px #1738251a; }
      span { display: inline-grid; width: 36px; height: 36px; place-items: center; border-radius: 50%; color: #fff; background: #61967f; }
      h1 { margin: 14px 0 8px; font-size: 22px; }
      p { margin: 0; color: #415468; }
      button { min-width: 44px; min-height: 44px; margin-top: 18px; padding: 0 18px; border: 0; border-radius: 12px; color: #fff; background: #a93615; font-weight: 700; }
    </style>
  </head>
  <body>
    <main>
      <span aria-hidden="true">K</span>
      <h1>AI-консультант</h1>
      <p>Готов помочь разобраться в услугах и выбрать следующий шаг.</p>
      <button type="button">Начать диалог</button>
    </main>
  </body>
</html>`;
}

const test = base.extend<{ builderApi: FakeBuilderApi }>({
  builderApi: async ({ page }, use) => {
    const requests: BuilderRequestLog[] = [];
    const snapshots = new Map<string, FakeBuilderSnapshot>();
    const seedRun = (runId: string, overrides: Partial<FakeBuilderSnapshot> = {}) => {
      const next = snapshot(runId, overrides);
      snapshots.set(runId, next);
      return next;
    };

    for (const runId of Object.keys(eventMatrix)) seedRun(runId);
    await installEventSource(page);

    await page.route('**/builder/api/runs**', async (route) => {
      const requestUrl = new URL(route.request().url());
      const marker = '/builder/api/runs';
      const suffix = requestUrl.pathname.slice(requestUrl.pathname.indexOf(marker) + marker.length);
      const parts = suffix.split('/').filter(Boolean).map(decodeURIComponent);
      const method = route.request().method();
      const body = parseBody(route);
      requests.push({ method, pathname: requestUrl.pathname, body });

      if (parts.length === 0 && method === 'POST') {
        const input = body as Partial<FakeBuilderRequest> | null;
        const running = snapshot('run-created', {
          request: request(input?.source_url ?? 'https://example.com'),
          status: 'running',
          artifact: null,
          latest_sequence: 0,
          quality_status: 'pending',
          usage: {
            prompt_tokens: 0,
            output_tokens: 0,
            thinking_tokens: 0,
            total_tokens: 0,
          },
          elapsed_seconds: 0,
        });
        seedRun('run-created', { request: running.request });
        await route.fulfill({ status: 202, json: running });
        return;
      }

      const [runId, action] = parts;
      if (!runId) {
        await route.fulfill({ status: 404, json: { error: { code: 'not_found' } } });
        return;
      }

      if (action === 'preview' && method === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'text/html; charset=utf-8',
          body: previewHtml(),
        });
        return;
      }

      if (action === 'cancel' && method === 'POST') {
        const cancelled = seedRun(runId, { status: 'cancelled', cancel_requested: true });
        await route.fulfill({ status: 202, json: { run_id: cancelled.run_id, cancel_requested: true } });
        return;
      }

      if ((action === 'retry' || action === 'refine') && method === 'POST') {
        const nextId = action === 'retry' ? 'run-created' : 'run-resume';
        await route.fulfill({ status: 202, json: seedRun(nextId) });
        return;
      }

      if (!action && method === 'GET') {
        const next = snapshots.get(runId);
        if (!next) {
          await route.fulfill({ status: 404, json: { error: { code: 'run_not_found' } } });
          return;
        }
        await route.fulfill({ status: 200, json: next });
        return;
      }

      await route.fulfill({ status: 404, json: { error: { code: 'not_found' } } });
    });

    await use({ requests, seedRun });
  },
});

export { expect, test };
