import { createReadStream } from 'node:fs';
import { stat } from 'node:fs/promises';
import { createServer } from 'node:http';
import { extname, join, normalize, resolve } from 'node:path';

const port = Number.parseInt(process.env.KAIGO_VISUAL_QA_PORT ?? '4175', 10);
const distRoot = resolve(process.cwd(), 'dist');
const projectId = '46c6a329-4c2e-5c99-927d-afc28de4311d';
const runId = '104de13f-669c-4fff-91fa-f9788a37b394';
const versionId = 'aab086de-7bb3-4818-99ec-8e63fd2c5e0c';
const changeRequest = 'пусть будет название RFN Assistant а также анимацию при наведении на закрытый виджет поменяй, и сделай анимацию интересную закрытие виджета';
const publicSummary = 'В RFN Assistant обновлена реакция закрытого launcher: при наведении активируются орбитальный контур, координатная сетка и подпись. Закрытие панели теперь ощущается как сборка терминала обратно в нижнюю точку маршрута за счёт выразительного обратного перехода и вращения кнопки закрытия.';

const preview = {
  id: 'artifact-rfn-v2',
  schema_version: '2',
  revision: 2,
  stage: 'validation',
  art_direction: 'Технологичный помощник для RFN Protection',
  body_html: '<main>RFN Assistant</main>',
  css: '',
  javascript: '',
  theme_tokens: { accent: '#ff6534', ink: '#111a20' },
  suggested_actions: ['Подобрать решение', 'Задать вопрос'],
  change_summary: publicSummary,
  layout_contract: { width: 'compact', placement: 'bottom-right' },
  quality_status: 'verified',
  source: 'accepted_artifact',
};

const events = [{
  sequence: 1,
  type: 'stage.completed',
  message: publicSummary,
  payload: { status: 'completed', stage: 'validation', revision: 2 },
  created_at: '2026-08-21T10:10:00Z',
}];

const run = {
  id: runId,
  project_id: projectId,
  mode: 'express',
  status: 'completed',
  state: 'completed',
  progress: 100,
  current_stage: 'validation',
  last_completed_stage: 'validation',
  error_code: null,
  error_message: null,
  change_request: changeRequest,
  created_at: '2026-08-21T10:00:00Z',
  started_at: '2026-08-21T10:00:01Z',
  finished_at: '2026-08-21T10:10:00Z',
  latest_sequence: 1,
  events,
  preview,
};

const project = {
  id: projectId,
  tenant_id: 7,
  owner_user_id: 42,
  source_url: 'https://rfn-protection.com/',
  brief: 'AI-консультант по решениям RFN Protection.',
  status: 'completed',
  active_revision: 2,
  active_version_id: versionId,
  active_run: run,
  created_at: '2026-08-21T09:00:00Z',
  updated_at: '2026-08-21T10:10:00Z',
};

const versions = {
  active_version_id: versionId,
  versions: [
    {
      id: versionId,
      project_id: projectId,
      ordinal: 2,
      kind: 'refinement',
      change_request: changeRequest,
      parent_version_id: 'rfn-version-1',
      run_id: runId,
      artifact_id: preview.id,
      artifact_revision: 2,
      refinable: true,
      created_at: '2026-08-21T10:10:00Z',
    },
    {
      id: 'rfn-version-1',
      project_id: projectId,
      ordinal: 1,
      kind: 'initial',
      change_request: null,
      parent_version_id: null,
      run_id: 'rfn-run-1',
      artifact_id: 'artifact-rfn-v1',
      artifact_revision: 1,
      refinable: true,
      created_at: '2026-08-21T09:50:00Z',
    },
  ],
};

const widgetDocument = `<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{box-sizing:border-box}html,body{width:100%;height:100%;margin:0}body{font-family:Arial,sans-serif;background:linear-gradient(145deg,#f7f8f7,#e6e9e7);color:#142027;overflow:hidden}.site{height:100%;padding:28px;background:linear-gradient(135deg,#16242b 0 38%,transparent 38%)}.brand{color:#fff;font-weight:800;letter-spacing:.08em}.copy{margin-top:34%;max-width:260px;color:#32434b}.copy h1{font-size:28px;margin:0 0 12px}.copy p{font-size:14px;line-height:1.5;margin:0}.widget{position:absolute;right:20px;bottom:20px;width:calc(100% - 40px);max-width:320px;padding:18px;border:1px solid #cbd3cf;border-radius:18px;background:#fff;box-shadow:0 18px 46px #17242b2b}.widget small{color:#e95124;font-weight:800;text-transform:uppercase}.widget h2{margin:7px 0 8px;font-size:20px}.widget p{margin:0;color:#53636b;font-size:13px;line-height:1.45}.actions{display:flex;gap:8px;margin-top:14px}.actions span{padding:8px 10px;border-radius:10px;background:#eef1ef;font-size:11px}.launcher{position:absolute;right:18px;bottom:18px;width:54px;height:54px;display:none;place-items:center;border-radius:50%;background:#ff6534;color:#fff;font-weight:900}
</style></head><body><main class="site"><div class="brand">RFN PROTECTION</div><div class="copy"><h1>Защита цифровой инфраструктуры</h1><p>Технологичные решения для современного бизнеса.</p></div></main><section class="widget"><small>AI-консультант</small><h2>RFN Assistant</h2><p>Здравствуйте! Помогу подобрать решение и отвечу на вопросы по продуктам RFN.</p><div class="actions"><span>Подобрать решение</span><span>Задать вопрос</span></div></section><div class="launcher">RFN</div></body></html>`;

const mimeTypes = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.webp': 'image/webp',
  '.woff2': 'font/woff2',
};

function json(response, payload, statusCode = 200) {
  response.writeHead(statusCode, { 'Content-Type': 'application/json; charset=utf-8' });
  response.end(JSON.stringify(payload));
}

async function staticResponse(requestPath, response) {
  let decodedPath;
  try {
    decodedPath = decodeURIComponent(requestPath);
  } catch {
    response.writeHead(400, { 'Content-Type': 'text/plain; charset=utf-8' });
    response.end('Malformed request path');
    return true;
  }
  const cleanPath = normalize(decodedPath).replace(/^(\.\.[/\\])+/, '');
  let filePath = resolve(join(distRoot, cleanPath === '/' ? 'index.html' : cleanPath.slice(1)));
  if (!filePath.startsWith(distRoot)) return false;
  try {
    const details = await stat(filePath);
    if (details.isDirectory()) filePath = join(filePath, 'index.html');
  } catch {
    filePath = join(distRoot, 'index.html');
  }
  response.writeHead(200, {
    'Content-Type': mimeTypes[extname(filePath)] ?? 'application/octet-stream',
    'Cache-Control': 'no-store',
  });
  createReadStream(filePath).pipe(response);
  return true;
}

createServer(async (request, response) => {
  const url = new URL(request.url ?? '/', `http://127.0.0.1:${port}`);
  if (url.pathname === '/api/auth/session') return json(response, {
    enabled: true, authenticated: true, user_id: 42, email: 'qa@example.invalid',
    csrf_token: 'visual-qa-csrf', pending_draft_id: null, providers: ['google'],
  });
  if (url.pathname === `/api/projects/${projectId}`) return json(response, project);
  if (url.pathname === `/api/projects/${projectId}/versions`) return json(response, versions);
  if (url.pathname === `/api/runs/${runId}`) return json(response, run);
  if (url.pathname === `/api/artifacts/${preview.id}`) return json(response, preview);
  if (url.pathname === '/api/billing/subscription') return json(response, { subscription: null });
  if (url.pathname === '/api/billing/payments/pending') return json(response, { payment: null, checkout_url: null });
  if (url.pathname === '/api/analytics/entry' || url.pathname === '/api/analytics/event') {
    response.writeHead(204); response.end(); return;
  }
  if (url.pathname === `/api/runs/${runId}/preview/document`) {
    response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    response.end(widgetDocument);
    return;
  }
  if (url.pathname.startsWith('/api/')) return json(response, { error: { code: 'visual_qa_not_implemented' } }, 404);
  await staticResponse(url.pathname, response);
}).listen(port, '127.0.0.1', () => {
  process.stdout.write(`Kaigo visual QA server: http://127.0.0.1:${port}\n`);
});
