import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { createServer } from 'node:net';
import { dirname, join, resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';

const requireFromFrontend = createRequire(new URL('../../../frontend/package.json', import.meta.url));
const { chromium } = requireFromFrontend('playwright');

const evidenceDir = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(evidenceDir, '../../..');
const frontendRoot = join(projectRoot, 'frontend');
const baseUrl = 'http://127.0.0.1:4174';
const projectId = 'manual-friendly-studio';

async function assertPortAvailable(port) {
  await new Promise((resolvePort, rejectPort) => {
    const probe = createServer();
    probe.once('error', rejectPort);
    probe.listen(port, '127.0.0.1', () => probe.close(resolvePort));
  });
}

async function waitForFrontend(apiProcess, viteProcess) {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    if (apiProcess.exitCode !== null || viteProcess.exitCode !== null) {
      throw new Error('A local evidence server stopped before the page became ready.');
    }
    try {
      const response = await fetch(`${baseUrl}/studio`);
      if (response.ok) return;
    } catch {
      // The local servers are still starting.
    }
    await delay(250);
  }
  throw new Error('The local evidence frontend did not become ready.');
}

function stop(processHandle) {
  if (processHandle.exitCode === null && !processHandle.killed) processHandle.kill();
}

async function settle(page) {
  await page.evaluate(async () => document.fonts.ready);
  await page.waitForTimeout(200);
}

async function screenshot(page, filename, fullPage = true) {
  await settle(page);
  await page.screenshot({
    path: join(evidenceDir, filename),
    fullPage,
    animations: 'disabled',
  });
}

await Promise.all([assertPortAvailable(4_174), assertPortAvailable(8_080)]);

const apiProcess = spawn(process.execPath, [
  join(evidenceDir, 'manual-studio-api.mjs'),
], {
  cwd: projectRoot,
  stdio: 'ignore',
  windowsHide: true,
});
const viteProcess = spawn(process.execPath, [
  join(frontendRoot, 'node_modules/vite/bin/vite.js'),
  '--host', '127.0.0.1', '--port', '4174', '--strictPort',
], {
  cwd: frontendRoot,
  stdio: 'ignore',
  windowsHide: true,
});

let browser;

try {
  await waitForFrontend(apiProcess, viteProcess);
  browser = await chromium.launch({ headless: true });
  const desktop = await browser.newPage({ viewport: { width: 1_536, height: 960 } });
  await desktop.goto(`${baseUrl}/studio`);
  await desktop.getByRole('heading', { name: 'Мои виджеты' }).waitFor();
  await screenshot(desktop, 'studio-library-desktop.png');

  await desktop.goto(`${baseUrl}/studio?project=${projectId}`);
  await desktop.getByTitle('Предпросмотр AI-сотрудника Kaigo').waitFor();
  await screenshot(desktop, 'studio-project-desktop.png');

  await desktop.getByRole('button', { name: 'Открыть версии' }).click();
  await desktop.getByRole('dialog', { name: 'История версий' }).waitFor();
  await screenshot(desktop, 'studio-versions-desktop.png');
  await desktop.keyboard.press('Escape');

  const mobile = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  await mobile.goto(`${baseUrl}/studio?project=${projectId}`);
  await mobile.getByRole('heading', { name: 'Чат с Kaigo' }).waitFor();
  await screenshot(mobile, 'studio-project-mobile-top.png', false);
  await mobile.getByRole('button', { name: 'Предпросмотр', exact: true }).click();
  await mobile.getByTitle('Предпросмотр AI-сотрудника Kaigo').waitFor();
  await mobile.getByRole('button', { name: 'На телефоне' }).click();
  await screenshot(mobile, 'studio-project-mobile.png');

  await desktop.goto(`${baseUrl}/studio?project=${projectId}`);
  await desktop.getByRole('button', { name: 'Открыть публикацию' }).click();
  await desktop.getByRole('heading', { name: 'Всё готово к публикации' }).waitFor();
  await desktop.getByLabel('На каких сайтах разрешить виджет').fill('https://atelier.example.com');
  await desktop.getByRole('button', { name: 'Опубликовать виджет' }).click();
  await desktop.getByRole('heading', { name: 'Виджет опубликован' }).waitFor();
  await desktop.getByText('Код для разработчика').click();
  await screenshot(desktop, 'studio-publication-desktop.png');
} finally {
  await browser?.close();
  stop(viteProcess);
  stop(apiProcess);
}
