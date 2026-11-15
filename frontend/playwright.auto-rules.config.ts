import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './scripts/auto-rules-browser', workers: 1,
  outputDir: 'test-results/auto-rules',
  reporter: [['list'], ['json', { outputFile: 'test-results/auto-rules.json' }]],
  use: { baseURL: 'http://127.0.0.1:5192', channel: process.env.PLAYWRIGHT_CHANNEL || undefined, trace: 'retain-on-failure' },
  webServer: { command: 'node scripts/education-browser/server.mjs', env: { EDUCATION_TEST_PORT: '5192' }, url: 'http://127.0.0.1:5192/scripts/auto-rules-browser/index.html', reuseExistingServer: false },
});
