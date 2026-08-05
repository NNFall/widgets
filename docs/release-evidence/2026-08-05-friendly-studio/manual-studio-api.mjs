import http from 'node:http';

const configuredPort = Number.parseInt(process.env.PORT ?? '8080', 10);
const port = Number.isInteger(configuredPort) && configuredPort > 0 && configuredPort <= 65_535
  ? configuredPort
  : 8080;

const projectId = 'manual-friendly-studio';
const runId = 'manual-friendly-run';
const versionId = 'manual-friendly-version-2';
const now = '2026-08-05T10:15:00.000Z';
const finished = '2026-08-05T10:15:24.800Z';

const events = [
  {
    sequence: 1,
    type: 'run.created',
    message: 'raw run created',
    payload: { status: 'queued' },
    created_at: now,
  },
  {
    sequence: 2,
    type: 'visual_audit.passed',
    message: 'raw audit passed',
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
    created_at: finished,
  },
];

const artifact = {
  id: 'manual-friendly-artifact-4',
  schema_version: '2',
  revision: 4,
  stage: 'motion_polish',
  art_direction: 'Тёплый, спокойный AI-консультант в фирменной палитре сайта.',
  body_html: '<main>Manual preview</main>',
  css: 'body { margin: 0; }',
  javascript: '',
  theme_tokens: { accent: '#fe6936', ink: '#071b2f' },
  suggested_actions: ['Узнать об услугах', 'Оставить заявку'],
  change_summary: 'Добавлены понятный диалог, мобильная компоновка и мягкая анимация.',
  layout_contract: { width: 'compact', placement: 'bottom-right' },
  quality_status: 'verified',
  source: 'accepted_artifact',
};

const run = {
  id: runId,
  project_id: projectId,
  mode: 'express',
  status: 'completed',
  state: 'completed',
  progress: 100,
  current_stage: 'motion_polish',
  last_completed_stage: 'motion_polish',
  error_code: null,
  error_message: null,
  created_at: now,
  started_at: now,
  finished_at: finished,
  latest_sequence: 2,
  events,
  preview: artifact,
};

const project = {
  id: projectId,
  tenant_id: 7,
  owner_user_id: 42,
  source_url: 'https://atelier.example.com/services',
  brief: 'Спокойный консультант, который объясняет услуги простым языком.',
  status: 'completed',
  active_revision: 4,
  active_version_id: versionId,
  active_run: run,
  created_at: now,
  updated_at: finished,
};

const versions = {
  active_version_id: versionId,
  versions: [
    {
      id: versionId,
      project_id: projectId,
      ordinal: 2,
      kind: 'refinement',
      change_request: 'Сделай приветствие короче, добавь больше воздуха и сохрани спокойный тон бренда. '.repeat(4).trim(),
      parent_version_id: 'manual-friendly-version-1',
      run_id: runId,
      artifact_id: artifact.id,
      artifact_revision: 4,
      refinable: true,
      created_at: finished,
    },
    {
      id: 'manual-friendly-version-1',
      project_id: projectId,
      ordinal: 1,
      kind: 'initial',
      change_request: null,
      parent_version_id: null,
      run_id: runId,
      artifact_id: 'manual-friendly-artifact-3',
      artifact_revision: 3,
      refinable: true,
      created_at: now,
    },
  ],
};

let publicationState = null;

const previewHtml = `<!doctype html>
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

function sendJson(response, body, status = 200) {
  response.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
  });
  response.end(JSON.stringify(body));
}

const server = http.createServer((request, response) => {
  const url = new URL(request.url ?? '/', `http://127.0.0.1:${port}`);
  const method = request.method ?? 'GET';

  if (method === 'GET' && url.pathname === '/api/auth/session') {
    sendJson(response, {
      enabled: true,
      authenticated: true,
      user_id: 42,
      email: 'owner@example.com',
      csrf_token: 'manual-friendly-csrf',
      pending_draft_id: null,
      providers: ['google', 'yandex'],
    });
    return;
  }
  if (method === 'GET' && url.pathname === '/api/projects') {
    sendJson(response, { projects: [project] });
    return;
  }
  if (method === 'GET' && url.pathname === `/api/projects/${projectId}`) {
    sendJson(response, project);
    return;
  }
  if (method === 'GET' && url.pathname === `/api/projects/${projectId}/versions`) {
    sendJson(response, versions);
    return;
  }
  if (method === 'GET' && url.pathname.startsWith('/api/artifacts/')) {
    const revision = url.pathname.endsWith('-3') ? 3 : 4;
    sendJson(response, { ...artifact, id: url.pathname.split('/').at(-1), revision });
    return;
  }
  if (method === 'GET' && url.pathname === `/api/runs/${runId}`) {
    sendJson(response, run);
    return;
  }
  if (method === 'GET' && url.pathname === `/api/runs/${runId}/events`) {
    response.writeHead(200, {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-store',
    });
    const event = events.at(-1);
    response.end(`id: ${event.sequence}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`);
    return;
  }
  if (method === 'GET' && url.pathname === `/api/runs/${runId}/preview/document`) {
    response.writeHead(200, {
      'Content-Type': 'text/html; charset=utf-8',
      'Cache-Control': 'no-store',
    });
    response.end(previewHtml);
    return;
  }
  if (method === 'GET' && url.pathname === '/api/billing/subscription') {
    sendJson(response, {
      subscription: {
        id: 'manual-friendly-subscription',
        plan_code: 'starter_monthly',
        status: 'active',
        current_period_start: now,
        current_period_end: '2026-09-05T10:15:00.000Z',
        auto_renew: true,
        next_renewal_at: '2026-09-05T10:15:00.000Z',
        generation_tokens_remaining: 750_000,
      },
    });
    return;
  }
  if (method === 'GET' && url.pathname === `/api/projects/${projectId}/publication`) {
    sendJson(response, { publication: publicationState });
    return;
  }
  if (method === 'POST' && url.pathname === `/api/projects/${projectId}/publish`) {
    const release = {
      release_id: 'manual-friendly-release-1',
      artifact_id: artifact.id,
      project_version_id: versionId,
      previous_release_id: null,
      revision: 4,
      checksum: 'manual-friendly-checksum',
      created_at: finished,
    };
    publicationState = {
      publication_id: 'manual-friendly-publication',
      stable_key: 'manual-friendly-widget',
      state: 'published',
      allowed_domains: ['https://atelier.example.com'],
      embed_url: 'https://widgets.kaigo.space/embed/manual-friendly-widget.js',
      runtime_url: 'https://widgets.kaigo.space/runtime/manual-friendly-widget',
      active_release: release,
      releases: [release],
    };
    sendJson(response, {
      publication_id: publicationState.publication_id,
      release_id: release.release_id,
      artifact_id: release.artifact_id,
      project_version_id: release.project_version_id,
      stable_key: publicationState.stable_key,
      revision: release.revision,
      allowed_domains: publicationState.allowed_domains,
      checksum: release.checksum,
      embed_url: publicationState.embed_url,
      runtime_url: publicationState.runtime_url,
    }, 201);
    return;
  }

  sendJson(response, { error: { code: 'manual_fixture_not_found' } }, 404);
});

server.listen(port, '127.0.0.1', () => {
  console.log(`Manual Friendly Studio API ready at http://127.0.0.1:${port}`);
});
