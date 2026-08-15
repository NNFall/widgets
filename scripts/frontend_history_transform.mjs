import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const allowedViews = new Set([
  'landing',
  'studio',
  'tour',
  'privacy',
  'personal-data-consent',
  'terms',
  'offer',
]);

const viewLabels = {
  landing: 'Лендинг',
  studio: 'Studio',
  tour: 'Product tour',
  privacy: 'Политика конфиденциальности',
  'personal-data-consent': 'Согласие на обработку данных',
  terms: 'Условия использования',
  offer: 'Предварительная оферта',
};

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function normalizeBasePath(basePath) {
  if (!/^\/frontend\/v[1-9][0-9]*\/$/.test(basePath)) {
    throw new Error(`invalid archive base path: ${basePath}`);
  }
  return basePath;
}

export function rewriteAssetPaths(source, basePath) {
  const normalized = normalizeBasePath(basePath);
  return source
    .replace(/(["'`])\/assets\//g, `$1${normalized}assets/`)
    .replace(/url\(\s*(["']?)\/assets\//g, `url($1${normalized}assets/`);
}

export function rewriteRuntimePaths(source) {
  return source
    .replace(/(["'`])\/api\//g, '$1/frontend-preview-api/api/')
    .replace(/(["'`])\/builder\//g, '$1/frontend-preview-api/');
}

export function injectArchiveBootstrap(source, version, view) {
  if (!/^v[1-9][0-9]*$/.test(version)) throw new Error(`invalid archive version: ${version}`);
  if (!allowedViews.has(view)) throw new Error(`invalid archive view: ${view}`);
  if (source.includes('data-archive-version=')) return source;

  const moduleMarker = '<script type="module"';
  const markerIndex = source.indexOf(moduleMarker);
  if (markerIndex < 0) throw new Error('frontend index does not contain a module script');

  const bootstrap = `<script src="/frontend/archive-bootstrap.js" data-archive-version="${version}" data-archive-view="${view}"></script>`;
  return `${source.slice(0, markerIndex)}${bootstrap}${source.slice(markerIndex)}`;
}

export function renderViewWrapper(version, title, view) {
  if (!/^v[1-9][0-9]*$/.test(version)) throw new Error(`invalid archive version: ${version}`);
  if (!allowedViews.has(view) || view === 'landing') throw new Error(`invalid wrapped view: ${view}`);
  const safeTitle = escapeHtml(title);
  const viewLabel = viewLabels[view];
  return `<!doctype html>
<html lang="ru">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="robots" content="noindex,nofollow,noarchive" />
    <title>${viewLabel}: ${safeTitle}</title>
    <style>html,body{width:100%;height:100%;margin:0;overflow:hidden;background:#303c42}iframe{display:block;width:100%;height:100%;border:0}</style>
  </head>
  <body>
    <iframe src="/frontend/${version}/${view}-runtime.html" title="Архив Kaigo: ${safeTitle}"></iframe>
  </body>
</html>
`;
}

export function renderStudioWrapper(version, title) {
  return renderViewWrapper(version, title, 'studio');
}

function renderViewLink(version, view) {
  const suffix = view === 'landing' ? '' : `${view}/`;
  return `<a href="/frontend/${version}/${suffix}">${viewLabels[view]}<span aria-hidden="true">↗</span></a>`;
}

export function renderHistoryIndex(versions) {
  const versionCount = versions.length;
  const milestones = versions.map((version, index) => {
    const views = version.views.filter((view) => allowedViews.has(view));
    const number = String(index + 1).padStart(2, '0');
    return `<article class="milestone">
      <div class="milestone__number">${number}</div>
      <div class="milestone__content">
        <p class="milestone__meta">${escapeHtml(version.date)} <span>${escapeHtml(version.commit.slice(0, 7))}</span></p>
        <h2>${escapeHtml(version.title)}</h2>
        <p>${escapeHtml(version.description)}</p>
      </div>
      <nav aria-label="Открыть ${escapeHtml(version.title)}">${views.map((view) => renderViewLink(version.id, view)).join('')}</nav>
    </article>`;
  }).join('\n');

  return `<!doctype html>
<html lang="ru">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="robots" content="noindex,nofollow,noarchive" />
    <title>История интерфейса Kaigo</title>
    <style>
      :root{color-scheme:light;--paper:oklch(0.975 0.008 76);--ink:oklch(0.25 0.018 225);--metal:oklch(0.9 0.012 225);--muted:oklch(0.52 0.02 225);--accent:oklch(0.68 0.19 42);font-family:Manrope,"Segoe UI",system-ui,sans-serif}
      *{box-sizing:border-box}body{margin:0;color:var(--ink);background:var(--paper)}a{color:inherit}.shell{width:min(1160px,calc(100% - 40px));margin:0 auto;padding:44px 0 72px}.topline{display:flex;align-items:center;justify-content:space-between;padding-bottom:34px;border-bottom:1px solid color-mix(in oklch,var(--ink) 16%,transparent)}.brand{display:flex;align-items:center;gap:12px;font-weight:800;font-size:20px}.brand i{width:38px;height:38px;display:grid;place-items:center;border-radius:10px;color:var(--paper);background:var(--accent);font-style:normal;font-size:22px}.production{min-height:44px;padding:0 16px;display:inline-flex;align-items:center;border:1px solid color-mix(in oklch,var(--ink) 18%,transparent);border-radius:11px;text-decoration:none;font-size:13px;font-weight:750}.production:hover,.production:focus-visible{border-color:var(--accent);outline:none}.intro{padding:70px 0 58px;display:grid;grid-template-columns:minmax(0,1.35fr) minmax(280px,.65fr);gap:60px;align-items:end}.eyebrow{margin:0 0 16px;color:var(--accent);font-size:12px;font-weight:850;letter-spacing:.12em;text-transform:uppercase}.intro h1{max-width:800px;margin:0;font-size:56px;line-height:1.02;letter-spacing:-.055em}.intro__note{max-width:48ch;margin:0;color:var(--muted);font-size:16px;line-height:1.65}.timeline{border-top:1px solid color-mix(in oklch,var(--ink) 16%,transparent)}.milestone{min-height:190px;padding:32px 0;display:grid;grid-template-columns:72px minmax(0,1fr) auto;gap:28px;align-items:start;border-bottom:1px solid color-mix(in oklch,var(--ink) 16%,transparent)}.milestone__number{width:50px;height:50px;display:grid;place-items:center;border-radius:50%;color:var(--accent);background:color-mix(in oklch,var(--accent) 9%,var(--paper));font-size:12px;font-weight:850}.milestone__meta{margin:1px 0 10px;color:var(--accent);font-size:11px;font-weight:850;letter-spacing:.08em;text-transform:uppercase}.milestone__meta span{margin-left:8px;color:var(--muted);font-family:ui-monospace,SFMono-Regular,Consolas,monospace;letter-spacing:0;text-transform:none}.milestone h2{margin:0;font-size:25px;line-height:1.15;letter-spacing:-.035em}.milestone__content>p:last-child{max-width:68ch;margin:12px 0 0;color:var(--muted);font-size:14px;line-height:1.6}.milestone nav{display:flex;flex-wrap:wrap;gap:8px;align-self:center}.milestone nav a{min-height:44px;padding:0 14px;display:inline-flex;align-items:center;gap:10px;border:1px solid color-mix(in oklch,var(--ink) 18%,transparent);border-radius:10px;text-decoration:none;font-size:13px;font-weight:800;white-space:nowrap}.milestone nav a:first-child{color:var(--paper);border-color:var(--ink);background:var(--ink)}.milestone nav a:hover,.milestone nav a:focus-visible{border-color:var(--accent);outline:2px solid color-mix(in oklch,var(--accent) 30%,transparent);outline-offset:2px}.milestone nav a span{color:var(--accent);font-size:15px}@media(max-width:760px){.shell{width:min(100% - 28px,680px);padding-top:22px}.topline{padding-bottom:22px}.production{font-size:0;padding-inline:13px}.production::after{content:'Текущий сайт';font-size:12px}.intro{padding:42px 0 38px;grid-template-columns:1fr;gap:22px}.intro h1{font-size:38px}.intro__note{font-size:14px}.milestone{padding:26px 0;grid-template-columns:48px minmax(0,1fr);gap:14px}.milestone__number{width:40px;height:40px}.milestone nav{grid-column:2;justify-self:start;flex-wrap:wrap}.milestone h2{font-size:22px}}
    </style>
  </head>
  <body>
    <main class="shell">
      <header class="topline"><div class="brand"><i>K</i>Kaigo</div><a class="production" href="/">Открыть текущий сайт</a></header>
      <section class="intro"><div><p class="eyebrow">Архив разработки</p><h1>История интерфейса Kaigo</h1></div><p class="intro__note">${versionCount} сохранённых этапов: от первого friendly Studio до текущего product tour. Каждая ссылка открывает неизменяемую сборку конкретного коммита.</p></section>
      <section class="timeline" aria-label="Версии интерфейса">${milestones}</section>
    </main>
  </body>
</html>
`;
}

async function collectTextFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) files.push(...await collectTextFiles(path));
    if (entry.isFile() && /\.(?:css|html|js)$/.test(entry.name)) files.push(path);
  }
  return files;
}

export async function transformDist(distDirectory, version) {
  const basePath = `/frontend/${version.id}/`;
  for (const path of await collectTextFiles(distDirectory)) {
    const source = await readFile(path, 'utf8');
    const transformed = rewriteRuntimePaths(rewriteAssetPaths(source, basePath));
    if (/(["'`])\/(?:api|builder)\//.test(transformed)) {
      throw new Error(`production API path remains in archived output: ${path}`);
    }
    await writeFile(path, transformed, 'utf8');
  }

  const indexPath = resolve(distDirectory, 'index.html');
  const rewrittenIndex = await readFile(indexPath, 'utf8');
  await writeFile(indexPath, injectArchiveBootstrap(rewrittenIndex, version.id, 'landing'), 'utf8');

  for (const view of version.views.filter((candidate) => candidate !== 'landing')) {
    const runtimePath = resolve(distDirectory, `${view}-runtime.html`);
    await writeFile(runtimePath, injectArchiveBootstrap(rewrittenIndex, version.id, view), 'utf8');
    const wrapperDirectory = resolve(distDirectory, view);
    await mkdir(wrapperDirectory, { recursive: true });
    await writeFile(
      resolve(wrapperDirectory, 'index.html'),
      renderViewWrapper(version.id, version.title, view),
      'utf8',
    );
  }
}

async function readManifest(path) {
  const versions = JSON.parse(await readFile(path, 'utf8'));
  if (!Array.isArray(versions) || versions.length === 0) throw new Error('history manifest must contain versions');
  return versions;
}

async function main(args) {
  const [command, manifestPath, target, versionId] = args;
  if (!command || !manifestPath || !target) {
    throw new Error('usage: frontend_history_transform.mjs <index|transform> <manifest.json> <output|dist> [version]');
  }
  const versions = await readManifest(resolve(manifestPath));

  if (command === 'index') {
    await writeFile(resolve(target), renderHistoryIndex(versions), 'utf8');
    return;
  }
  if (command === 'transform') {
    const version = versions.find((candidate) => candidate.id === versionId);
    if (!version) throw new Error(`unknown archive version: ${versionId}`);
    await transformDist(resolve(target), version);
    return;
  }
  throw new Error(`unknown command: ${command}`);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).catch((error) => {
    console.error(error instanceof Error ? error.message : error);
    process.exitCode = 1;
  });
}
