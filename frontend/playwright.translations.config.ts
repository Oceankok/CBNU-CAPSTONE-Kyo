import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './scripts/education-browser',
  testMatch: 'translations.spec.ts',
  outputDir: 'test-results/translations',
  workers: 1,
  reporter: [['list'], ['json', { outputFile: 'test-results/translations.json' }]],
  use: { baseURL: 'http://127.0.0.1:5188', channel: process.env.PLAYWRIGHT_CHANNEL || undefined, trace: 'retain-on-failure' },
  webServer: {
    command: 'node scripts/education-browser/server.mjs',
    env: { EDUCATION_TEST_PORT: '5188' },
    url: 'http://127.0.0.1:5188/scripts/education-browser/index.html',
    reuseExistingServer: false,
  },
});
