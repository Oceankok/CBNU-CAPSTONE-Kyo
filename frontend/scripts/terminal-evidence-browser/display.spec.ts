import { test, expect, type Page } from '@playwright/test';
import acceptance from './acceptance-media.json' with { type: 'json' };
import detail from './acceptance-detail.json' with { type: 'json' };

// Saved real API responses from fresh verification, combined exactly as the new
// read-only /media projection. Synthetic capture/video provenance, no credentials.
const clone = () => structuredClone(acceptance);
const open = (page: Page, review = false) => page.goto('/scripts/terminal-evidence-browser/index.html'+(review ? '?review' : ''));
async function serve(page: Page, media: ReturnType<typeof clone>) {
  const calls: string[] = [];
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (!path.startsWith('/api/')) return route.continue();
    calls.push(route.request().method()+' '+path);
    const body = path.endsWith('/media') ? media : path === '/api/events/'+detail.event.event_id ? detail
      : path.endsWith('/saved-suspected-relations') ? { items: [] }
      : { items: [], candidates: [], total: 0, eligible: false };
    return route.fulfill({ json: body });
  });
  await page.route('**/storage/**', route => route.fulfill({ status: 200, contentType: 'video/mp4', body: '' }));
  return calls;
}
const requestRow = (page: Page, id: string) => page.getByTestId('clip-request-'+id);

test('real ReviewDetail shows terminal source failure while preserving received primary and review gates', async ({ page }, testInfo) => {
  const media = clone(); const calls = await serve(page, media);
  await open(page, true);
  for (const camera of ['FV_EMPTY', 'FV_MISSING']) {
    await expect(page.getByTestId('capture-report-'+camera)).toContainText('영상 확보 불가');
    await expect(page.getByTestId('capture-report-'+camera)).toContainText('원본 수집 실패');
    for (const request of media.clip_requests.filter(row => row.camera_id === camera)) {
      await expect(requestRow(page, request.request_id)).toContainText('영상 확보 불가 · 원본 수집 실패');
      await expect(requestRow(page, request.request_id)).not.toContainText('수신 대기');
    }
  }
  const received = media.clip_requests.find(row => row.camera_id === 'FV_B' && row.status === 'received')!;
  await expect(requestRow(page, received.request_id)).toContainText('영상 확인 가능');
  await expect(page.getByRole('button', { name: /확정 위반/ })).toBeDisabled();
  await page.getByRole('button', { name: '▶ 클립 재생' }).click();
  await expect(page.getByRole('dialog').locator('video')).toHaveAttribute('controls', '');
  await page.getByRole('button', { name: '닫기', exact: true }).click();
  await page.screenshot({ path: testInfo.outputPath('terminal-evidence-pass.png'), fullPage: true });
  expect(calls.every(call => call.startsWith('GET '))).toBe(true);
});

test('captured but not uploaded remains pending and polls; redaction remains processing', async ({ page }) => {
  const media = clone();
  const pending = media.clip_requests.find(row => row.camera_id === 'FV_B')!;
  pending.status = 'pending'; media.items = media.items.filter(item => item.clip_request_id !== pending.request_id);
  const processing = media.items.find(item => item.camera_id === 'FV_B')!;
  processing.status = 'processing'; processing.url = null;
  await page.clock.install();
  const calls = await serve(page, media); await open(page);
  await expect(requestRow(page, pending.request_id)).toContainText('영상 수신 대기');
  await expect(requestRow(page, processing.clip_request_id!)).toContainText('얼굴 가림 처리 중');
  const before = calls.length; await page.clock.fastForward(5100);
  await expect.poll(() => calls.length).toBeGreaterThan(before);
});

test('partial missing range preserves pending captured portion; collecting report never becomes terminal', async ({ page }) => {
  const media = clone();
  const request = media.clip_requests.find(row => row.camera_id === 'FV_A' && row.status === 'pending')!;
  const segment = media.collection.segments.find(part => part.request_id === request.request_id)!;
  segment.capture_start_at = '2026-11-15T08:39:46.200000Z';
  await serve(page, media); await open(page);
  await expect(requestRow(page, request.request_id)).toContainText('일부 영상 확보 불가');
  await expect(requestRow(page, request.request_id)).toContainText('확보된 구간은 수신 대기');
  media.collection.cameras.find(row => row.camera_id === 'FV_A')!.capture_report!.state = 'collecting';
  await page.getByRole('button', { name: '영상 상태 새로고침' }).click();
  await expect(requestRow(page, request.request_id)).toHaveText('FV_A: 영상 수신 대기');
});

test('terminal missing requests do not poll forever; old API without collection retains waiting behavior', async ({ page }) => {
  const media = clone(); await page.clock.install();
  const calls = await serve(page, media); await open(page);
  await expect(page.getByTestId('capture-report-FV_EMPTY')).toBeVisible();
  const before = calls.length; await page.clock.fastForward(15100);
  expect(calls.length).toBe(before);
  delete (media as Partial<typeof media>).collection;
  await page.getByRole('button', { name: '영상 상태 새로고침' }).click();
  const request = media.clip_requests.find(row => row.camera_id === 'FV_EMPTY')!;
  await expect(requestRow(page, request.request_id)).toHaveText('FV_EMPTY: 영상 수신 대기');
});

test('open collection without segment requests still refreshes to receive final evidence reports', async ({ page }) => {
  const media = clone(); media.clip_requests = []; media.items = [];
  media.collection.phase = 'open';
  for (const camera of media.collection.cameras) camera.capture_report = null;
  await page.clock.install(); const calls = await serve(page, media); await open(page);
  await expect(page.getByRole('region', { name: '카메라별 영상 확보 상태' })).toBeVisible();
  const before = calls.length; await page.clock.fastForward(5100);
  await expect.poll(() => calls.length).toBeGreaterThan(before);
});
