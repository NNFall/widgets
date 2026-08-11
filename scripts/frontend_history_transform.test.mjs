import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

import {
  injectArchiveBootstrap,
  renderHistoryIndex,
  renderStudioWrapper,
  rewriteAssetPaths,
  rewriteRuntimePaths,
} from './frontend_history_transform.mjs';

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');

const versions = [
  {
    id: 'v1',
    commit: 'f88c792c8364007af619658c8e9dace028850b1e',
    date: '5 августа',
    title: 'Первый friendly Studio',
    description: 'Первая понятная версия рабочего пространства.',
    views: ['landing', 'studio'],
  },
  {
    id: 'v2',
    commit: 'cc69735d0e094656739077c19d0e6ee0986c8797',
    date: '5 августа',
    title: 'Studio в один экран',
    description: 'Чат и предпросмотр собраны в одном пространстве.',
    views: ['studio'],
  },
];

test('rewrites only root-relative public assets into the immutable version path', () => {
  const source = [
    'const hero = "/assets/hero.webp";',
    "const logo = '/assets/logo.svg';",
    'background:url(/assets/grid.png)',
    'const built = "/frontend/v1/assets/index-ABC12345.js";',
  ].join('\n');

  const transformed = rewriteAssetPaths(source, '/frontend/v1/');

  assert.match(transformed, /"\/frontend\/v1\/assets\/hero\.webp"/);
  assert.match(transformed, /'\/frontend\/v1\/assets\/logo\.svg'/);
  assert.match(transformed, /url\(\/frontend\/v1\/assets\/grid\.png\)/);
  assert.equal(
    transformed.match(/\/frontend\/v1\/assets\/index-ABC12345\.js/g)?.length,
    1,
  );
});

test('rewrites every root-relative production API surface into the preview namespace', () => {
  const source = [
    'fetch("/api/projects")',
    '<a href="/api/auth/google/start">Google</a>',
    'iframe.src = `/api/runs/${runId}/preview/document`;',
    'const builderBase = "/builder/";',
    'const external = "https://example.com/api/reference";',
  ].join('\n');

  const transformed = rewriteRuntimePaths(source);

  assert.match(transformed, /fetch\("\/frontend-preview-api\/api\/projects"\)/);
  assert.match(transformed, /href="\/frontend-preview-api\/api\/auth\/google\/start"/);
  assert.match(transformed, /`\/frontend-preview-api\/api\/runs\/\$\{runId\}\/preview\/document`/);
  assert.match(transformed, /builderBase = "\/frontend-preview-api\/"/);
  assert.match(transformed, /https:\/\/example\.com\/api\/reference/);
  assert.doesNotMatch(transformed, /(["'`])\/(?:api|builder)\//);
});

test('archive fetch rewrite preserves the complete Request, including a POST body', async () => {
  const bootstrap = await readFile(
    resolve(repositoryRoot, 'deploy/frontend-history/archive-bootstrap.js'),
    'utf8',
  );
  const calls = [];
  const browserWindow = {
    EventSource: class NativeEventSource {},
    fetch: async (...args) => {
      calls.push(args);
      return { ok: true };
    },
    history: { replaceState() {} },
    location: { origin: 'https://kaigo.space' },
  };
  const browserDocument = {
    currentScript: { dataset: { archiveVersion: 'v1', archiveView: 'studio' } },
  };

  vm.runInNewContext(bootstrap, {
    document: browserDocument,
    Request,
    URL,
    window: browserWindow,
  });

  const original = new Request('https://kaigo.space/api/projects', {
    body: JSON.stringify({ name: 'Архив' }),
    headers: { 'Content-Type': 'application/json', 'X-Archive-Test': 'yes' },
    method: 'POST',
  });
  await browserWindow.fetch(original);

  assert.equal(calls.length, 1);
  const [rewritten] = calls[0];
  assert.ok(rewritten instanceof Request);
  assert.equal(rewritten.url, 'https://kaigo.space/frontend-preview-api/api/projects');
  assert.equal(rewritten.method, 'POST');
  assert.equal(rewritten.headers.get('X-Archive-Test'), 'yes');
  assert.deepEqual(JSON.parse(await rewritten.text()), { name: 'Архив' });
});

test('injects the shared archive bootstrap before the application module', () => {
  const source = '<!doctype html><html><head><script type="module" src="/frontend/v1/assets/app.js"></script></head><body></body></html>';

  const transformed = injectArchiveBootstrap(source, 'v1', 'studio');

  assert.match(
    transformed,
    /<script src="\/frontend\/archive-bootstrap\.js" data-archive-version="v1" data-archive-view="studio"><\/script><script type="module"/,
  );
});

test('renders a full-viewport Studio wrapper without changing the archived visual', () => {
  const wrapper = renderStudioWrapper('v2', 'Studio в один экран');

  assert.match(wrapper, /<iframe[^>]+src="\/frontend\/v2\/studio-runtime\.html"/);
  assert.match(wrapper, /title="Архив Kaigo: Studio в один экран"/);
  assert.match(wrapper, /width:100%;height:100%;border:0/);
});

test('renders chronological navigation with only the views available for each milestone', () => {
  const html = renderHistoryIndex(versions);

  assert.match(html, /История интерфейса Kaigo/);
  assert.match(html, /href="\/frontend\/v1\/"[^>]*>Лендинг</);
  assert.match(html, /href="\/frontend\/v1\/studio\/"[^>]*>Studio</);
  assert.doesNotMatch(html, /href="\/frontend\/v2\/"[^>]*>Лендинг</);
  assert.match(html, /f88c792/);
  assert.match(html, /cc69735/);
});

test('nginx isolates preview credentials and redirects from production sessions', async () => {
  const nginx = await readFile(
    resolve(repositoryRoot, 'deploy/frontend-history/nginx.conf'),
    'utf8',
  );

  assert.match(nginx, /proxy_set_header Cookie "";/);
  assert.match(nginx, /proxy_set_header Authorization "";/);
  assert.match(nginx, /proxy_hide_header Set-Cookie;/);
  assert.match(nginx, /proxy_pass http:\/\/127\.0\.0\.1:18110\//);
  assert.match(nginx, /proxy_redirect \/api\/ \/frontend-preview-api\/api\//);
});

test('deployment cleanup covers explicit exits and only removes an owned worktree', async () => {
  const script = await readFile(
    resolve(repositoryRoot, 'scripts/deploy_frontend_history.sh'),
    'utf8',
  );

  assert.match(script, /worktree_owned=0/);
  assert.match(script, /trap cleanup EXIT/);
  assert.match(script, /trap 'exit 130' INT/);
  assert.match(script, /trap 'exit 143' TERM/);
  assert.match(script, /worktree_owned}" -eq 1/);
});
