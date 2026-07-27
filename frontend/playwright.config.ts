import { defineConfig } from '@playwright/test';

const port = 4_174;

export default defineConfig({
  testDir: './e2e',
  outputDir: './test-results',
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 45_000,
  expect: {
    timeout: 10_000,
    toHaveScreenshot: {
      animations: 'disabled',
      caret: 'hide',
      threshold: 0.25,
    },
  },
  snapshotPathTemplate: '{testDir}/{testFilePath}-snapshots/{arg}-{projectName}-{platform}{ext}',
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    colorScheme: 'light',
    locale: 'ru-RU',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: `npm run dev -- --host 127.0.0.1 --port ${port} --strictPort`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  projects: [
    {
      name: 'desktop-1920',
      grep: /@desktop/,
      use: { viewport: { width: 1_920, height: 1_080 } },
    },
    {
      name: 'mobile-390',
      grep: /@mobile/,
      use: {
        viewport: { width: 390, height: 844 },
        hasTouch: true,
        isMobile: true,
      },
    },
    {
      name: 'reduced-motion',
      grep: /@reduced/,
      use: {
        viewport: { width: 1_920, height: 1_080 },
        reducedMotion: 'reduce',
      },
    },
    {
      name: 'accessibility',
      grep: /@a11y/,
      use: {
        viewport: { width: 1_920, height: 1_080 },
        reducedMotion: 'reduce',
      },
    },
  ],
});
