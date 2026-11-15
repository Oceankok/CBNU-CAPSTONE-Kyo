import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './scripts/terminal-evidence-browser', workers: 1,
  outputDir: 'test-results/terminal-evidence',
  reporter: [['list'], ['json', { outputFile: 'test-results/terminal-evidence.json' }]],
  use: { baseURL: 'http://127.0.0.1:5194', channel: process.env.PLAYWRIGHT_CHANNEL || undefined, trace: 'retain-on-failure' },
  webServer: { command: 'node scripts/education-browser/server.mjs', env: { EDUCATION_TEST_PORT: '5194' }, url: 'http://127.0.0.1:5194/scripts/terminal-evidence-browser/index.html', reuseExistingServer: false },
});
