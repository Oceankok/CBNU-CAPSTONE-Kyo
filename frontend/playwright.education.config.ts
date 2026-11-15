import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './scripts/education-browser',
  testIgnore: 'translations.spec.ts',
  fullyParallel: false,
  workers: 1,
  reporter: [['list'], ['json', { outputFile: 'test-results/education.json' }]],
  use: {
    baseURL: 'http://127.0.0.1:5187',
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'node scripts/education-browser/server.mjs',
    url: 'http://127.0.0.1:5187/scripts/education-browser/index.html',
    reuseExistingServer: false,
  },
});
