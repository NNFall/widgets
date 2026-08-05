import { test as base, expect, type Route } from '@playwright/test';

const PROJECT_ID = '5d34bcab-cfd3-4ca2-8398-eb59f34aab92';
const CSRF_TOKEN = 'playwright-csrf';
const NOW = '2026-07-27T12:00:00.000Z';
const FINISHED = '2026-07-27T12:00:24.800Z';
const ACTIVE_VERSION_ID = 'version-playwright-2';
const LONG_CHANGE_REQUEST = 'Сделай приветствие короче, добавь больше воздуха и сохрани спокойный тон бренда. '.repeat(4).trim();

type RunStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';

interface RequestLog {
  method: string;
  pathname: string;
  body: unknown;
  headers: Record<string, string>;
}

interface FakeRun {
  id: string;
  project_id: string;
  mode: 'express';
  status: RunStatus;
  state: RunStatus;
  progress: number;
  current_stage: 'motion_polish' | null;
  last_completed_stage: 'motion_polish' | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  latest_sequence: number;
  events: FakeEvent[];
  preview: FakePreview | null;
}

interface FakeProject {
  id: string;
  tenant_id: number;
  owner_user_id: number;
  source_url: string;
  brief: string | null;
  status: string;
  active_revision: number | null;
  active_version_id: string | null;
  active_run: FakeRun | null;
  created_at: string;
  updated_at: string;
}

interface FakeEvent {
  sequence: number;
  type: string;
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
}

interface FakePreview {
  id: string;
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
  quality_status: 'verified';
  source: 'accepted_artifact';
}

export interface FakeBuilderApi {
  projectId: string;
  csrfToken: string;
  requests: RequestLog[];
  seedRun: (runId: string) => FakeRun;
  seedRunningRun: (runId: string) => FakeRun;
  activateSubscription: () => void;
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

function preview(): FakePreview {
  return {
    id: 'artifact-playwright-4',
    schema_version: '2',
    revision: 4,
    stage: 'motion_polish',
    art_direction: 'Тёплый, спокойный AI-консультант в фирменной палитре сайта.',
    body_html: '<main>Fake preview</main>',
    css: 'body { margin: 0; }',
    javascript: '',
    theme_tokens: { accent: '#fe6936', ink: '#071b2f' },
    suggested_actions: ['Узнать об услугах', 'Оставить заявку'],
    change_summary: 'Добавлены понятный диалог, мобильная компоновка и мягкая анимация.',
    layout_contract: { width: 'compact', placement: 'bottom-right' },
    quality_status: 'verified',
    source: 'accepted_artifact',
  };
}

function artifactFor(id: string, revision: number): FakePreview {
  return {
    ...preview(),
    id,
    revision,
    art_direction: `Проверенная концепция ${revision}`,
  };
}

function events(): FakeEvent[] {
  return [
    {
      sequence: 1,
      type: 'run.created',
      message: 'Запуск создан',
      payload: { status: 'queued' },
      created_at: NOW,
    },
    {
      sequence: 2,
      type: 'visual_audit.passed',
      message: 'Визуальная проверка пройдена',
      payload: {
        status: 'completed',
        stage: 'motion_polish',
        revision: 4,
        changes: ['css', 'javascript'],
        usage: {
          prompt_tokens: 8_400,
          output_tokens: 2_100,
          thinking_tokens: 1_845,
          total_tokens: 12_345,
        },
      },
      created_at: FINISHED,
    },
  ];
}

function completedRun(runId: string): FakeRun {
  return {
    id: runId,
    project_id: PROJECT_ID,
    mode: 'express',
    status: 'completed',
    state: 'completed',
    progress: 100,
    current_stage: 'motion_polish',
    last_completed_stage: 'motion_polish',
    error_code: null,
    error_message: null,
    created_at: NOW,
    started_at: NOW,
    finished_at: FINISHED,
    latest_sequence: 2,
    events: events(),
    preview: preview(),
  };
}

function queuedRun(runId: string): FakeRun {
  return {
    ...completedRun(runId),
    status: 'queued',
    state: 'queued',
    progress: 0,
    current_stage: null,
    last_completed_stage: null,
    started_at: null,
    finished_at: null,
    latest_sequence: 0,
    events: [],
    preview: null,
  };
}

function runningRun(runId: string): FakeRun {
  const created = events()[0];
  return {
    ...queuedRun(runId),
    status: 'running',
    state: 'running',
    progress: 34,
    current_stage: 'motion_polish',
    started_at: NOW,
    latest_sequence: 1,
    events: [created],
  };
}

function createProject(activeRun: FakeRun | null = null): FakeProject {
  return {
    id: PROJECT_ID,
    tenant_id: 7,
    owner_user_id: 42,
    source_url: 'https://example.com',
    brief: 'Уверенный консультант, который говорит простым языком.',
    status: activeRun?.status ?? 'draft',
    active_revision: activeRun?.preview?.revision ?? null,
    active_version_id: activeRun?.status === 'completed' ? ACTIVE_VERSION_ID : null,
    active_run: activeRun,
    created_at: NOW,
    updated_at: FINISHED,
  };
}

function versionsFor(activeRun: FakeRun | null) {
  if (activeRun?.status !== 'completed') {
    return { active_version_id: null, versions: [] };
  }
  return {
    active_version_id: ACTIVE_VERSION_ID,
    versions: [
      {
        id: ACTIVE_VERSION_ID,
        project_id: PROJECT_ID,
        ordinal: 2,
        kind: 'refinement',
        change_request: LONG_CHANGE_REQUEST,
        parent_version_id: 'version-playwright-1',
        run_id: activeRun.id,
        artifact_id: 'artifact-playwright-4',
        artifact_revision: 4,
        refinable: true,
        created_at: FINISHED,
      },
      {
        id: 'version-playwright-1',
        project_id: PROJECT_ID,
        ordinal: 1,
        kind: 'initial',
        change_request: null,
        parent_version_id: null,
        run_id: activeRun.id,
        artifact_id: 'artifact-playwright-3',
        artifact_revision: 3,
        refinable: true,
        created_at: NOW,
      },
    ],
  };
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

function sse(event: FakeEvent) {
  return `id: ${event.sequence}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`;
}

const test = base.extend<{ builderApi: FakeBuilderApi }>({
  builderApi: async ({ page }, use) => {
    const requests: RequestLog[] = [];
    let project = createProject();
    const runs = new Map<string, FakeRun>();
    const pendingRefresh = new Set<string>();
    let subscriptionActive = false;
    let publicationRevision = 0;
    let publicationState: Record<string, unknown> | null = null;

    const seedRun = (runId: string) => {
      const run = completedRun(runId);
      runs.set(runId, run);
      project = createProject(run);
      return run;
    };
    const seedRunningRun = (runId: string) => {
      const run = runningRun(runId);
      runs.set(runId, run);
      project = createProject(run);
      return run;
    };

    await page.route('**/api/**', async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const method = request.method();
      const body = parseBody(route);
      requests.push({
        method,
        pathname: url.pathname,
        body,
        headers: request.headers(),
      });

      if (url.pathname === '/api/auth/session' && method === 'GET') {
        await route.fulfill({
          status: 200,
          json: {
            enabled: true,
            authenticated: true,
            user_id: 42,
            email: 'owner@example.com',
            csrf_token: CSRF_TOKEN,
            pending_draft_id: null,
            providers: ['google', 'yandex'],
          },
        });
        return;
      }

      if (url.pathname === '/api/projects' && method === 'POST') {
        const input = body as { url?: string; brief?: string } | null;
        project = {
          ...createProject(),
          source_url: input?.url ?? 'https://example.com',
          brief: input?.brief ?? '',
        };
        await route.fulfill({ status: 201, json: project });
        return;
      }
      if (url.pathname === '/api/projects' && method === 'GET') {
        await route.fulfill({ status: 200, json: { projects: [project] } });
        return;
      }

      const projectMatch = url.pathname.match(/^\/api\/projects\/([^/]+)$/);
      if (projectMatch && method === 'GET') {
        await route.fulfill({ status: 200, json: project });
        return;
      }
      if (projectMatch && method === 'PATCH') {
        const input = body as { url?: string; brief?: string } | null;
        project = {
          ...project,
          source_url: input?.url ?? project.source_url,
          brief: input?.brief ?? project.brief,
        };
        await route.fulfill({ status: 200, json: project });
        return;
      }

      const versionsMatch = url.pathname.match(/^\/api\/projects\/([^/]+)\/versions$/);
      if (versionsMatch && method === 'GET') {
        await route.fulfill({ status: 200, json: versionsFor(project.active_run) });
        return;
      }

      const artifactMatch = url.pathname.match(/^\/api\/artifacts\/([^/]+)$/);
      if (artifactMatch && method === 'GET') {
        const artifactId = decodeURIComponent(artifactMatch[1]);
        const revision = artifactId.endsWith('-3') ? 3 : 4;
        await route.fulfill({ status: 200, json: artifactFor(artifactId, revision) });
        return;
      }

      const createRunMatch = url.pathname.match(/^\/api\/projects\/([^/]+)\/runs$/);
      if (createRunMatch && method === 'POST') {
        const runId = 'run-created';
        const run = queuedRun(runId);
        runs.set(runId, completedRun(runId));
        pendingRefresh.add(runId);
        project = createProject(run);
        await route.fulfill({ status: 202, json: run });
        return;
      }

      const cancelRunMatch = url.pathname.match(/^\/api\/runs\/([^/]+)\/cancel$/);
      if (cancelRunMatch && method === 'POST') {
        const runId = decodeURIComponent(cancelRunMatch[1]);
        const current = runs.get(runId) ?? runningRun(runId);
        const cancelled: FakeRun = {
          ...current,
          status: 'cancelled',
          state: 'cancelled',
          finished_at: FINISHED,
        };
        runs.set(runId, cancelled);
        project = createProject(cancelled);
        await route.fulfill({
          status: 202,
          json: { run_id: runId, cancel_requested: true, status: current.status },
        });
        return;
      }

      const retryRunMatch = url.pathname.match(/^\/api\/runs\/([^/]+)\/retry$/);
      if (retryRunMatch && method === 'POST') {
        const sourceRunId = decodeURIComponent(retryRunMatch[1]);
        const replacementId = `${sourceRunId}-retry`;
        const queued = queuedRun(replacementId);
        runs.set(replacementId, completedRun(replacementId));
        pendingRefresh.add(replacementId);
        project = createProject(queued);
        await route.fulfill({ status: 202, json: queued });
        return;
      }

      const publishMatch = url.pathname.match(/^\/api\/projects\/([^/]+)\/publish$/);
      if (publishMatch && method === 'POST') {
        publicationRevision += 1;
        const input = body as {
          artifact_id?: string;
          project_version_id?: string;
          revision?: number;
          allowed_domains?: string[];
        } | null;
        const published = {
          publication_id: 'publication-playwright',
          release_id: `release-playwright-${publicationRevision}`,
          artifact_id: input?.artifact_id ?? 'artifact-playwright-4',
          project_version_id: input?.project_version_id ?? null,
          stable_key: 'stable-playwright-widget',
          revision: input?.revision ?? 4,
          allowed_domains: input?.allowed_domains ?? ['https://example.com'],
          checksum: `checksum-${publicationRevision}`,
          embed_url: 'https://widgets.kaigo.space/embed/stable-playwright-widget.js',
          runtime_url: 'https://widgets.kaigo.space/runtime/stable-playwright-widget',
        };
        const release = {
          release_id: published.release_id,
          artifact_id: published.artifact_id,
          project_version_id: published.project_version_id,
          previous_release_id: null,
          revision: published.revision,
          checksum: published.checksum,
          created_at: NOW,
        };
        publicationState = {
          publication_id: published.publication_id,
          stable_key: published.stable_key,
          state: 'published',
          allowed_domains: published.allowed_domains,
          embed_url: published.embed_url,
          runtime_url: published.runtime_url,
          active_release: release,
          releases: [release],
        };
        await route.fulfill({
          status: publicationRevision === 1 ? 201 : 200,
          json: published,
        });
        return;
      }

      const publicationStateMatch = url.pathname.match(/^\/api\/projects\/([^/]+)\/publication$/);
      if (publicationStateMatch && method === 'GET') {
        await route.fulfill({
          status: 200,
          json: { publication: publicationState },
        });
        return;
      }

      const rollbackMatch = url.pathname.match(/^\/api\/publications\/([^/]+)\/rollback$/);
      if (rollbackMatch && method === 'POST') {
        await route.fulfill({
          status: 200,
          json: {
            publication_id: 'publication-playwright',
            release_id: (body as { target_release_id?: string } | null)?.target_release_id,
            artifact_id: 'artifact-playwright-4',
            project_version_id: ACTIVE_VERSION_ID,
            stable_key: 'stable-playwright-widget',
            revision: 4,
            allowed_domains: ['https://example.com'],
            checksum: 'checksum-rollback',
            embed_url: 'https://widgets.kaigo.space/embed/stable-playwright-widget.js',
            runtime_url: 'https://widgets.kaigo.space/runtime/stable-playwright-widget',
          },
        });
        return;
      }

      const eventsMatch = url.pathname.match(/^\/api\/runs\/([^/]+)\/events$/);
      if (eventsMatch && method === 'GET') {
        const runId = decodeURIComponent(eventsMatch[1]);
        const run = runs.get(runId) ?? completedRun(runId);
        await route.fulfill({
          status: 200,
          contentType: 'text/event-stream; charset=utf-8',
          body: sse(run.events.at(-1) ?? events()[1]),
        });
        return;
      }

      const previewMatch = url.pathname.match(/^\/api\/runs\/([^/]+)\/preview\/document$/);
      if (previewMatch && method === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'text/html; charset=utf-8',
          body: previewHtml(),
        });
        return;
      }

      const runMatch = url.pathname.match(/^\/api\/runs\/([^/]+)$/);
      if (runMatch && method === 'GET') {
        const runId = decodeURIComponent(runMatch[1]);
        const run = runs.get(runId);
        if (!run) {
          await route.fulfill({ status: 404, json: { error: { code: 'run_not_found' } } });
          return;
        }
        if (pendingRefresh.has(runId)) {
          pendingRefresh.delete(runId);
          project = createProject(run);
        }
        await route.fulfill({ status: 200, json: run });
        return;
      }

      if (url.pathname === '/api/billing/subscription' && method === 'GET') {
        await route.fulfill({
          status: 200,
          json: {
            subscription: subscriptionActive
              ? {
                  id: 'subscription-playwright',
                  plan_code: 'starter_monthly',
                  status: 'active',
                  current_period_start: NOW,
                  current_period_end: '2026-08-27T12:00:00.000Z',
                  auto_renew: true,
                  next_renewal_at: '2026-08-27T12:00:00.000Z',
                  generation_tokens_remaining: 750_000,
                }
              : null,
          },
        });
        return;
      }

      if (url.pathname === '/api/billing/payments/pending' && method === 'GET') {
        await route.fulfill({ status: 200, json: { payment: null, checkout_url: null } });
        return;
      }

      await route.fulfill({ status: 404, json: { error: { code: 'not_found' } } });
    });

    await use({
      projectId: PROJECT_ID,
      csrfToken: CSRF_TOKEN,
      requests,
      seedRun,
      seedRunningRun,
      activateSubscription: () => { subscriptionActive = true; },
    });
  },
});

export { expect, test };
