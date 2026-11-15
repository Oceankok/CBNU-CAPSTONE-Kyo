import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './scripts/zone-rules-browser',
  workers: 1,
  outputDir: '.private_media/zone-rules-browser/artifacts',
  reporter: [
    ['list'],
    ['json', { outputFile: '.private_media/zone-rules-browser/results.json' }],
  ],
  use: {
    baseURL: 'http://127.0.0.1:5189',
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'node scripts/zone-rules-browser/server.mjs',
    url: 'http://127.0.0.1:5189/scripts/zone-rules-browser/index.html',
    reuseExistingServer: false,
  },
});
