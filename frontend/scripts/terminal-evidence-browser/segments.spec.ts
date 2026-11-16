import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import acceptance from './acceptance-media.json' with { type: 'json' };

// Reuse an independently encoded synthetic H264 clip, never real camera footage.
// Supply PPE_SEGMENT_TEST_VIDEO (21.933333-second fixture) when running this case.
for (const gap of [false, true]) test(`real decoded segments ${gap ? 'stop at gap' : 'continue beyond thirty seconds'}`, async ({ page }) => {
  const bytes = readFileSync(process.env.PPE_SEGMENT_TEST_VIDEO!);
  const media = structuredClone(acceptance);
  const template = media.items.find(m => m.camera_id === 'FV_B' && m.url)!;
  const origin = Date.parse('2026-11-15T08:00:00Z');
  const stamp = (ms: number) => new Date(origin + ms).toISOString();
  media.items = [0, 1].map(i => ({ ...template, media_id: `part-${i}`, clip_request_id: `request-${i}`, url: `/storage/part-${i}.mp4` }));
  // Reverse response order: initial playback must still choose sequence zero.
  media.items.reverse();
  media.items.push({ ...template, camera_id:'FV_A', role:'reference', media_id:'ref-part', clip_request_id:'ref-request', url:'/storage/ref-part.mp4' });
  media.clip_requests = [];
  media.collection.cameras = media.collection.cameras.filter(c => c.camera_id === 'FV_B' || c.camera_id === 'FV_A');
  media.collection.phase = 'closed';
  media.collection.segments = [0, 1].map(i => ({ ...media.collection.segments[0], camera_id: 'FV_B', sequence: i, request_id: `request-${i}`, capture_start_at: stamp(i * 21933 + (gap && i ? 1000 : 0)), capture_end_at: stamp((i + 1) * 21933 + (gap && i ? 1000 : 0)) }));
  await page.route('**/api/**', route => { const path = new URL(route.request().url()).pathname; return path.startsWith('/api/') ? route.fulfill({ json: media }) : route.continue(); });
  await page.route('**/storage/**', route => { const range=route.request().headers()['range']; if (!range) return route.fulfill({status:200,contentType:'video/mp4',body:bytes,headers:{'Accept-Ranges':'bytes'}}); const match=/bytes=(\d+)-(\d*)/.exec(range); if(!match) throw Error(range); const start=Number(match[1]), end=match[2]?Math.min(Number(match[2]),bytes.length-1):bytes.length-1; return route.fulfill({status:206,contentType:'video/mp4',body:bytes.subarray(start,end+1),headers:{'Accept-Ranges':'bytes','Content-Range':`bytes ${start}-${end}/${bytes.length}`}}); });
  await page.goto('/scripts/terminal-evidence-browser/index.html');
  await page.getByRole('button', { name: '▶ 클립 재생' }).click();
  const player = page.getByRole('dialog').locator('video');
  await expect(player).toHaveAttribute('src', /part-0/);
  await player.evaluate(async v => { const video = v as HTMLVideoElement; await video.play(); video.pause(); });
  const pausedSrc = await player.getAttribute('src');
  await page.waitForTimeout(300);
  expect(await player.getAttribute('src')).toBe(pausedSrc);
  // Real decode/duration and natural ended; no synthetic ended event.
  await expect.poll(() => player.evaluate(v => (v as HTMLVideoElement).duration)).toBeGreaterThan(21);
  await player.evaluate(async v => { const video = v as HTMLVideoElement; video.playbackRate = 16; video.currentTime = video.duration - .25; await video.play(); });
  if (gap) {
    await expect(page.getByRole('status')).toContainText('시간 공백');
    await expect(player).toHaveAttribute('src', /part-0/);
  } else {
    await expect(player).toHaveAttribute('src', /part-1/);
    await expect.poll(() => player.evaluate(v => (v as HTMLVideoElement).currentTime)).toBeGreaterThan(.05);
    await player.evaluate(v => (v as HTMLVideoElement).pause());
    await page.waitForTimeout(300);
    await expect(player).toHaveAttribute('src', /part-1/);
  }
  if (!gap) { await player.evaluate(async v => { const video=v as HTMLVideoElement; video.playbackRate=16; video.currentTime=video.duration-.25; await video.play(); }); await expect.poll(() => player.evaluate((v:HTMLVideoElement)=>v.ended)).toBe(true); await expect(player).toHaveAttribute('src', /part-1/); }
  await page.getByRole('button', { name: '닫기', exact: true }).click();
  await page.getByRole('tab', { name: /영상 1/ }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
});
