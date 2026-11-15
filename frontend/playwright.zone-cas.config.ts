import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './scripts/zone-rules-browser',
  testMatch: 'zone-revisions.spec.ts',
  workers: 1,
  outputDir: '.private_media/zone-cas-browser/artifacts',
  reporter: [
    ['list'],
    ['json', { outputFile: '.private_media/zone-cas-browser/results.json' }],
  ],
  use: {
    baseURL: 'http://127.0.0.1:5191',
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'node scripts/zone-rules-browser/server.mjs',
    env: { ZONE_RULES_TEST_PORT: '5191' },
    url: 'http://127.0.0.1:5191/scripts/zone-rules-browser/index.html',
    reuseExistingServer: false,
  },
});
