import { test, expect, type Page, type Route } from '@playwright/test';

const target = (n: number, ppe = 'helmet', quarter = '2026-Q4') => ({
  recommendation_id: `${quarter}-r${n}`, recommendation_rank: n, ppe_type: ppe,
  zone_name: `Zone ${n}`, education_topic: 'Safety', priority_score: 30 - n,
  score_version: 'zone_ppe_v2', generated_at: '2026-11-15T00:00:00Z',
  score_breakdown: { confirmed_count: 2, repeat_weeks: 1, period_weeks: 13, process_risk_weight: 1 },
});
const list = (count: number, quarter = '2026-Q4') => ({ quarter, generated_at: null, items: Array.from({ length: count }, (_, i) => target(i + 1, 'helmet', quarter)) });
const json = (route: Route, value: unknown, status = 200) => route.fulfill({ status, json: value });
async function setup(page: Page, handler: (route: Route, url: URL) => Promise<unknown>) {
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://127.0.0.1:5187') throw new Error('External request blocked');
    if (!url.pathname.startsWith('/api/')) return route.continue();
    return void await handler(route, url);
  });
}
const go = (page: Page) => page.goto('/scripts/education-browser/index.html?kind=recommend');

test('G1: top three cards and 21 full targets are independent; 20-zone and PPE limits', async ({ page }) => {
  let previewIds: string[] = [];
  await setup(page, async (route, url) => {
    if (url.pathname.endsWith('/targets')) return json(route, { ...list(21), items: [...list(21).items, target(22, 'vest')] });
    if (url.pathname.endsWith('/recommendations')) return json(route, list(3));
    if (url.pathname.endsWith('/preview')) {
      previewIds = route.request().postDataJSON().recommendation_ids;
      return json(route, { quarter: '2026-Q4', ppe_type: 'helmet', snapshot: 's1', confirmed_count_sum: 40, sources: [] });
    }
    return json(route, { items: [] });
  });
  await go(page);
  const pool = page.getByRole('region', { name: '통합 교육 대상 선택' });
  await expect(pool.getByRole('checkbox')).toHaveCount(22);
  await expect(page.getByRole('button', { name: '교육 자료 작성·게시', exact: true })).toHaveCount(3);
  await pool.getByRole('checkbox').first().check();
  await expect(pool.getByRole('checkbox').last()).toBeDisabled();
  for (let i = 1; i < 20; i++) await pool.getByRole('checkbox').nth(i).check();
  await expect(pool.getByRole('checkbox').nth(20)).toBeDisabled();
  await page.getByRole('button', { name: '미리보기 조회', exact: true }).click();
  await expect(page.getByText('snapshot: s1')).toBeVisible();
  expect(previewIds).toHaveLength(20);
  expect(new Set(previewIds).size).toBe(20);
});

for (const status of [200, 400, 401, 403, 503]) {
  test(`G1: targets status ${status} never triggers generation`, async ({ page }) => {
    const writes: string[] = [];
    await setup(page, async (route, url) => {
      if (route.request().method() !== 'GET') writes.push(url.pathname);
      if (url.pathname.endsWith('/targets')) return json(route, status === 200 ? list(0) : { detail: 'target_read_failed' }, status);
      if (url.pathname.endsWith('/recommendations')) return json(route, list(3));
      return json(route, { items: [] });
    });
    await go(page);
    const pool = page.getByRole('region', { name: '통합 교육 대상 선택' });
    if (status === 200) await expect(pool).toContainText('저장된 통합 대상이 없습니다');
    else await expect(pool.getByRole('alert')).toContainText('target_read_failed');
    expect(writes).toEqual([]);
    await expect(page.getByRole('button', { name: '교육 자료 작성·게시', exact: true })).toHaveCount(3);
  });
}

test('G1: explicit regeneration stages new targets; applying invalidates preview and preserves draft', async ({ page }) => {
  let regenerated = false;
  let writes = 0;
  await setup(page, async (route, url) => {
    if (url.pathname.endsWith('/generate')) { regenerated = true; writes++; return json(route, list(3)); }
    if (url.pathname.endsWith('/targets')) return json(route, list(regenerated ? 4 : 3));
    if (url.pathname.endsWith('/recommendations')) return json(route, list(3));
    if (url.pathname.endsWith('/preview')) return json(route, { quarter: '2026-Q4', ppe_type: 'helmet', snapshot: regenerated ? 's2' : 's1', confirmed_count_sum: 4, sources: [] });
    return json(route, { items: [] });
  });
  await go(page);
  const pool = page.getByRole('region', { name: '통합 교육 대상 선택' });
  await pool.getByRole('checkbox').nth(0).check();
  await pool.getByRole('checkbox').nth(1).check();
  await page.getByRole('button', { name: '미리보기 조회', exact: true }).click();
  await page.getByRole('textbox', { name: '공통 교육 본문', exact: true }).fill('Preserved combined draft');
  await page.getByRole('button', { name: '추천·전체 대상 갱신', exact: true }).click();
  await expect(pool).toContainText('최신 통합 대상 4개');
  await expect(pool.getByRole('checkbox')).toHaveCount(3);
  await expect(page.getByText('snapshot: s1')).toBeVisible();
  await page.getByRole('button', { name: '최신 대상 적용·구역 다시 선택', exact: true }).click();
  await expect(pool.getByRole('checkbox')).toHaveCount(4);
  await expect(pool.locator('input:checked')).toHaveCount(0);
  await expect(page.getByText('snapshot: s1')).toHaveCount(0);
  await expect(page.getByRole('textbox', { name: '공통 교육 본문', exact: true })).toHaveValue('Preserved combined draft');
  expect(writes).toBe(1);
  await pool.getByRole('checkbox').nth(2).check();
  await pool.getByRole('checkbox').nth(3).check();
  await page.getByRole('button', { name: '미리보기 조회', exact: true }).click();
  await expect(page.getByText('snapshot: s2')).toBeVisible();
  await expect(page.getByRole('textbox', { name: '공통 교육 본문', exact: true })).toHaveValue('Preserved combined draft');
});

test('G1: old quarter target response cannot populate the new quarter', async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let oldRequested = false;
  await setup(page, async (route, url) => {
    const quarter = url.searchParams.get('quarter') ?? '2026-Q4';
    if (url.pathname.endsWith('/targets')) {
      if (quarter === '2026-Q4') { oldRequested = true; await gate; }
      return json(route, list(quarter === '2026-Q4' ? 4 : 2, quarter));
    }
    if (url.pathname.endsWith('/recommendations')) return json(route, list(3, quarter));
    return json(route, { items: [] });
  });
  await go(page);
  await expect.poll(() => oldRequested).toBe(true);
  await page.getByRole('button', { name: '이전 분기로 전환' }).click();
  const pool = page.getByRole('region', { name: '통합 교육 대상 선택' });
  await expect(pool.getByRole('checkbox')).toHaveCount(2);
  const response = page.waitForResponse(r => r.url().includes('/targets?quarter=2026-Q4'));
  release();
  await response;
  await expect(pool.getByRole('checkbox')).toHaveCount(2);
});

test('G1: selection 409 preserves input and requires explicit preview request', async ({ page }) => {
  let previews = 0;
  let creates = 0;
  await setup(page, async (route, url) => {
    if (url.pathname.endsWith('/targets')) return json(route, list(4));
    if (url.pathname.endsWith('/recommendations')) return json(route, list(3));
    if (url.pathname.endsWith('/preview')) { previews++; return json(route, { quarter: '2026-Q4', ppe_type: 'helmet', snapshot: `s${previews}`, confirmed_count_sum: 4, sources: [] }); }
    if (route.request().method() === 'POST') { creates++; return json(route, { detail: 'education_bundle_selection_changed' }, 409); }
    return json(route, { items: [] });
  });
  await go(page);
  const pool = page.getByRole('region', { name: '통합 교육 대상 선택' });
  await pool.getByRole('checkbox').nth(0).check();
  await pool.getByRole('checkbox').nth(1).check();
  await page.getByRole('button', { name: '미리보기 조회' }).click();
  await page.getByRole('textbox', { name: '제목', exact: true }).fill('Draft');
  await page.getByRole('textbox', { name: '공통 교육 본문', exact: true }).fill('My text');
  await page.getByRole('textbox', { name: '확인 체크리스트 (한 줄에 하나)', exact: true }).fill('Check');
  await page.getByRole('button', { name: '수동 초안 저장' }).click();
  await expect.poll(() => previews).toBe(2);
  await expect(page.getByRole('button', { name: '수동 초안 저장' })).toBeDisabled();
  await expect(page.getByRole('textbox', { name: '공통 교육 본문', exact: true })).toHaveValue('My text');
  expect(creates).toBe(1);
  await page.getByRole('button', { name: '미리보기 조회' }).click();
  await expect(page.getByText('snapshot: s3')).toBeVisible();
  await expect(page.getByRole('button', { name: '수동 초안 저장' })).toBeEnabled();
  expect(creates).toBe(1);
});

test('G1: initial missing recommendations refresh the target pool after generation', async ({ page }) => {
  let generated = false;
  let writes = 0;
  await setup(page, async (route, url) => {
    if (url.pathname.endsWith('/targets')) return json(route, list(generated ? 4 : 0));
    if (url.pathname.endsWith('/recommendations')) return json(route, { detail: 'not_found' }, 404);
    if (url.pathname.endsWith('/generate')) { generated = true; writes++; return json(route, list(3)); }
    return json(route, { items: [] });
  });
  await go(page);
  await expect(page.getByRole('region', { name: '통합 교육 대상 선택' }).getByRole('checkbox')).toHaveCount(4);
  expect(writes).toBe(1);
});

test('G1: explicit target application preserves single-material draft removed from top three', async ({ page }) => {
  let generated = false;
  await setup(page, async (route, url) => {
    if (url.pathname.endsWith('/targets')) return json(route, list(4));
    if (url.pathname.endsWith('/recommendations')) return json(route, list(3));
    if (url.pathname.endsWith('/generate')) { generated = true; return json(route, { ...list(3), items: [target(2), target(3), target(4)] }); }
    if (url.pathname.endsWith('/generation')) return json(route, { configured: false });
    return json(route, { items: [] });
  });
  await go(page);
  await page.getByRole('button', { name: '교육 자료 작성·게시', exact: true }).first().click();
  await page.getByRole('button', { name: '+ 새 초안 작성', exact: true }).click();
  await page.getByRole('textbox', { name: '제목', exact: true }).fill('Single draft');
  await page.getByRole('textbox', { name: '본문', exact: true }).fill('Preserved single text');
  await page.getByRole('button', { name: '추천·전체 대상 갱신', exact: true }).click();
  await page.getByRole('button', { name: '최신 대상 적용·구역 다시 선택', exact: true }).click();
  expect(generated).toBe(true);
  await expect(page.getByText(/이전 추천 대상\(2026-Q4-r1\)/)).toBeVisible();
  await page.getByRole('button', { name: '교육 자료 작성·게시', exact: true }).last().click();
  await expect(page.getByRole('textbox', { name: '본문', exact: true })).toHaveValue('Preserved single text');
});
