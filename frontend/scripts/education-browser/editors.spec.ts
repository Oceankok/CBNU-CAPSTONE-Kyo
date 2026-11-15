import { test, expect, type Page, type Route } from '@playwright/test';

const material = (id = 'm1', version = 0) => ({
  material_id: id, recommendation_id: 'r1', quarter: '2026-Q4', zone_name: 'A',
  ppe_type: 'helmet', education_topic: 'Safety', title: id, body: 'Original body', checklist: ['Original check'],
  status: 'draft', provider: 'manual', version, updated_by: 'admin', updated_at: '2026-11-15T00:00:00Z',
});
const bundle = (id = 'b1', status = 'draft', version = 0) => ({
  bundle_id: id, quarter: '2026-Q4', title: id, body: 'Original bundle body', checklist: ['Bundle check'],
  ppe_type: 'helmet', status, version, sources: [], updated_at: '2026-11-15T00:00:00Z',
});
const json = (route: Route, value: unknown, status = 200) => route.fulfill({ status, json: value });
const deferred = () => {
  let resolve!: () => void;
  const promise = new Promise<void>(done => { resolve = done; });
  return { promise, resolve };
};
async function setup(page: Page, handler: (route: Route, path: string) => Promise<unknown>) {
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://127.0.0.1:5187') throw new Error(`External request blocked: ${url.origin}`);
    if (url.pathname.startsWith('/api/')) return void await handler(route, url.pathname);
    return route.continue();
  });
}
async function openMaterial(page: Page) {
  await page.goto('/scripts/education-browser/index.html');
  await page.getByRole('button', { name: '교육 자료 작성·게시', exact: true }).click();
  await page.getByRole('button', { name: '수정', exact: true }).first().click();
}

test('F1: delayed save locks all draft controls and only clears the submitted draft', async ({ page }) => {
  const gate = deferred();
  let submitted: Record<string, unknown> | undefined;
  await setup(page, async (route, path) => {
    if (route.request().method() === 'PATCH') {
      submitted = route.request().postDataJSON();
      await gate.promise;
      return json(route, material('m1', 1));
    }
    if (path.endsWith('/materials')) return json(route, { items: [material(), material('m2')] });
    if (path.endsWith('/generation')) return json(route, { configured: false });
    throw new Error(path);
  });
  await openMaterial(page);
  await page.getByRole('textbox', { name: '본문', exact: true }).fill('Submitted body');
  await page.getByRole('button', { name: '수정 저장', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '본문', exact: true })).toBeDisabled();
  await expect(page.getByRole('textbox', { name: '제목', exact: true })).toBeDisabled();
  await expect(page.getByRole('textbox', { name: '확인 목록 (한 줄에 하나)', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: '취소', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: '수정', exact: true })).toBeDisabled();
  expect(submitted?.body).toBe('Submitted body');
  gate.resolve();
  await expect(page.getByTestId('draft')).toHaveText('null');
  await page.getByRole('button', { name: '수정', exact: true }).last().click();
  await page.getByRole('textbox', { name: '본문', exact: true }).fill('Next draft');
  await expect(page.getByTestId('draft')).toContainText('Next draft');
});

test('F1: response from an unmounted editor cannot clear its remounted draft', async ({ page }) => {
  const gate = deferred();
  await setup(page, async (route, path) => {
    if (route.request().method() === 'PATCH') { await gate.promise; return json(route, material('m1', 1)); }
    if (path.endsWith('/materials')) return json(route, { items: [material()] });
    return json(route, { configured: false });
  });
  await openMaterial(page);
  await page.getByRole('textbox', { name: '본문', exact: true }).fill('Preserved draft');
  await page.getByRole('button', { name: '수정 저장' }).click();
  await page.getByRole('button', { name: '외부 편집기 전환' }).click();
  await page.getByRole('button', { name: '외부 편집기 전환' }).click();
  await page.getByRole('button', { name: '교육 자료 작성·게시', exact: true }).click();
  await page.getByRole('textbox', { name: '본문', exact: true }).fill('Newer remounted draft');
  const completed = page.waitForResponse(response => response.request().method() === 'PATCH');
  gate.resolve();
  await (await completed).finished();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await expect(page.getByTestId('draft')).toContainText('Newer remounted draft');
  await expect(page.getByRole('textbox', { name: '본문', exact: true })).toHaveValue('Newer remounted draft');
});

test('F1: failed delayed save unlocks controls and preserves content before switching editors', async ({ page }) => {
  const gate = deferred();
  await setup(page, async (route, path) => {
    if (route.request().method() === 'PATCH') { await gate.promise; return json(route, { detail: 'unavailable' }, 503); }
    if (path.endsWith('/materials')) return json(route, { items: [material(), material('m2')] });
    return json(route, { configured: false });
  });
  await openMaterial(page);
  await page.getByRole('textbox', { name: '본문', exact: true }).fill('Unsaved text');
  await page.getByRole('button', { name: '수정 저장' }).click();
  await expect(page.getByRole('textbox', { name: '본문', exact: true })).toBeDisabled();
  gate.resolve();
  await expect(page.getByRole('textbox', { name: '본문', exact: true })).toBeEnabled();
  await expect(page.getByRole('textbox', { name: '본문', exact: true })).toHaveValue('Unsaved text');
  await expect(page.getByTestId('draft')).toContainText('Unsaved text');
  await page.getByRole('button', { name: '취소', exact: true }).click();
  await page.getByRole('button', { name: '수정', exact: true }).last().click();
  await expect(page.getByRole('textbox', { name: '제목', exact: true })).toHaveValue('m2');
});

test('F2: latest published material cannot be adopted for editing or overwritten', async ({ page }) => {
  let reads = 0;
  let writes = 0;
  await setup(page, async (route, path) => {
    if (route.request().method() === 'PATCH') { writes++; return json(route, { detail: 'published_material_read_only' }, 409); }
    if (path.endsWith('/materials')) { reads++; return json(route, { items: [{ ...material('m1', reads > 1 ? 1 : 0), status: reads > 1 ? 'published' : 'draft' }] }); }
    return json(route, { configured: false });
  });
  await openMaterial(page);
  await page.getByRole('textbox', { name: '본문', exact: true }).fill('Keep this text');
  await page.getByRole('button', { name: '수정 저장' }).click();
  await expect(page.getByRole('button', { name: '내 입력을 유지하고 최신 버전 선택' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '최신 서버본으로 다시 시작' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '게시 취소', exact: true })).toBeDisabled();
  await expect(page.getByRole('textbox', { name: '본문', exact: true })).toHaveValue('Keep this text');
  await expect(page.getByTestId('scope-conflicts')).toHaveText('0');
  expect(writes).toBe(1);
});

test('F3: failed row refresh can be retried without losing another editor', async ({ page }) => {
  let details = 0;
  let writes = 0;
  await setup(page, async (route, path) => {
    if (route.request().method() === 'POST') { writes++; return json(route, { detail: 'education_bundle_version_conflict' }, 409); }
    if (path.endsWith('/b1')) { details++; return details === 1 ? json(route, { detail: 'unavailable' }, 503) : json(route, { ...bundle('b1', 'draft', 1), body: 'Refreshed row' }); }
    return json(route, { items: [bundle(), bundle('b2')] });
  });
  await page.goto('/scripts/education-browser/index.html?kind=bundle');
  await page.getByRole('button', { name: /게시자료 목록 \(/ }).click();
  await page.locator('article').last().getByRole('button', { name: '검토·편집' }).click();
  await page.getByRole('textbox', { name: '공통 교육 본문', exact: true }).fill('Other draft');
  const row = page.locator('article').first();
  await row.getByRole('button', { name: '명시적으로 게시' }).click();
  await row.getByRole('alertdialog').getByRole('button', { name: '게시', exact: true }).click();
  await expect(row.getByRole('button', { name: '최신 자료 다시 조회' })).toBeEnabled();
  await expect(row.getByRole('button', { name: '명시적으로 게시' })).toBeDisabled();
  await page.getByRole('button', { name: '최신 자료 다시 조회' }).click();
  await expect(row.getByRole('alert')).toContainText('Refreshed row');
  await expect(page.getByRole('textbox', { name: '공통 교육 본문', exact: true })).toHaveValue('Other draft');
  expect(writes).toBe(1);
});

test('F3: active bundle save conflict requires review and resubmits only the selected version', async ({ page }) => {
  const versions: number[] = [];
  await setup(page, async (route, path) => {
    if (route.request().method() === 'PATCH') {
      versions.push(route.request().postDataJSON().expected_version);
      return versions.length === 1 ? json(route, { detail: 'education_bundle_version_conflict' }, 409) : json(route, { ...bundle('b1', 'draft', 2), body: 'My bundle draft' });
    }
    if (path.endsWith('/b1')) return json(route, { ...bundle('b1', 'draft', 1), body: 'Other bundle body' });
    return json(route, { items: [bundle(), bundle('b2')] });
  });
  await page.goto('/scripts/education-browser/index.html?kind=bundle');
  await page.getByRole('button', { name: /게시자료 목록 \(/ }).click();
  await page.locator('article').first().getByRole('button', { name: '검토·편집' }).click();
  await page.getByRole('textbox', { name: '공통 교육 본문', exact: true }).fill('My bundle draft');
  await page.getByRole('button', { name: '초안 저장', exact: true }).click();
  await expect(page.locator('p').filter({ hasText: '서버 본문:' })).toContainText('Other bundle body');
  await expect(page.getByRole('textbox', { name: '공통 교육 본문', exact: true })).toHaveValue('My bundle draft');
  await expect(page.getByRole('button', { name: '초안 저장', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '내 초안으로 해결' }).click();
  expect(versions).toEqual([0]);
  await page.getByRole('button', { name: '초안 저장', exact: true }).click();
  await expect.poll(() => versions).toEqual([0, 1]);
});

for (const choice of ['내 입력을 유지하고 최신 버전 선택', '최신 서버본으로 다시 시작']) {
  test(`F2: version conflict requires explicit resolution: ${choice}`, async ({ page }) => {
    const versions: number[] = [];
    let listReads = 0;
    let latest = material();
    await setup(page, async (route, path) => {
      if (route.request().method() === 'PATCH') {
        versions.push(route.request().postDataJSON().expected_version);
        if (versions.length === 1) { latest = { ...material('m1', 1), body: 'Other admin body', checklist: ['Other check'] }; return json(route, { detail: 'education_material_version_conflict' }, 409); }
        return json(route, material('m1', 2));
      }
      if (path.endsWith('/materials')) { listReads++; return json(route, { items: [latest] }); }
      return json(route, { configured: false });
    });
    await openMaterial(page);
    await page.getByRole('textbox', { name: '본문', exact: true }).fill('My draft');
    await page.getByRole('button', { name: '수정 저장' }).click();
    await expect(page.getByRole('alert')).toContainText('Other admin body');
    await expect(page.getByRole('alert')).toContainText('Other check');
    await expect(page.getByRole('textbox', { name: '본문', exact: true })).toHaveValue('My draft');
    await expect(page.getByRole('button', { name: '수정 저장' })).toBeDisabled();
    await expect(page.getByRole('button', { name: '검토 완료 · 게시' })).toHaveCount(0);
    expect(versions).toEqual([0]);
    expect(listReads).toBe(2);
    await expect(page.getByTestId('scope-conflicts')).toHaveText('0');
    await page.getByRole('button', { name: choice, exact: true }).click();
    await expect(page.getByRole('textbox', { name: '본문', exact: true })).toHaveValue(choice.startsWith('내 입력') ? 'My draft' : 'Other admin body');
    expect(versions).toEqual([0]);
    await page.getByRole('button', { name: '수정 저장' }).click();
    await expect(page.getByTestId('draft')).toHaveText('null');
    expect(versions).toEqual([0, 1]);
  });
}

test('F2: scope conflict preserves inputs and asks parent to explicitly reselect target', async ({ page }) => {
  let writes = 0;
  await setup(page, async (route, path) => {
    if (route.request().method() === 'POST') { writes++; return json(route, { detail: 'education_recommendation_changed' }, 409); }
    if (path.endsWith('/materials')) return json(route, { items: [] });
    return json(route, { configured: false });
  });
  await page.goto('/scripts/education-browser/index.html');
  await page.getByRole('button', { name: '교육 자료 작성·게시', exact: true }).click();
  await page.getByRole('button', { name: '+ 새 초안 작성', exact: true }).click();
  await page.getByRole('textbox', { name: '제목', exact: true }).fill('Draft title');
  await page.getByRole('textbox', { name: '본문', exact: true }).fill('Draft body');
  await page.getByRole('textbox', { name: '확인 목록 (한 줄에 하나)', exact: true }).fill('Check');
  await page.getByRole('button', { name: '초안 저장', exact: true }).click();
  await expect(page.getByTestId('scope-conflicts')).toHaveText('1');
  await expect(page.getByText(/추천 대상이 바뀌었습니다/)).toBeVisible();
  await expect(page.getByRole('textbox', { name: '본문', exact: true })).toHaveValue('Draft body');
  expect(writes).toBe(1);
});

test('F2: failed conflict refresh stays blocked until latest data is reviewed', async ({ page }) => {
  let reads = 0;
  let writes = 0;
  await setup(page, async (route, path) => {
    if (route.request().method() === 'PATCH') { writes++; return json(route, { detail: 'education_material_version_conflict' }, 409); }
    if (path.endsWith('/materials')) { reads++; return reads === 2 ? json(route, { detail: 'unavailable' }, 503) : json(route, { items: [material('m1', reads > 2 ? 1 : 0)] }); }
    return json(route, { configured: false });
  });
  await openMaterial(page);
  await page.getByRole('button', { name: '수정 저장' }).click();
  await expect(page.getByRole('button', { name: '최신 자료 다시 조회' })).toBeEnabled();
  await expect(page.getByRole('button', { name: '수정 저장' })).toBeDisabled();
  await page.getByRole('button', { name: '최신 자료 다시 조회' }).click();
  await expect(page.getByRole('alert')).toContainText('v1');
  expect(writes).toBe(1);
});

for (const action of ['publish', 'unpublish']) {
  for (const editingOther of [false, true]) {
    test(`F3: list ${action} refreshes row and preserves other draft=${editingOther}`, async ({ page }) => {
      const gate = deferred();
      const versions: number[] = [];
      const initial = bundle('b1', action === 'publish' ? 'draft' : 'published');
      let current = initial;
      await setup(page, async (route, path) => {
        if (route.request().method() === 'POST') {
          versions.push(route.request().postDataJSON().expected_version);
          if (versions.length === 1) return json(route, { detail: 'education_bundle_version_conflict' }, 409);
          current = { ...current, status: action === 'publish' ? 'published' : 'draft', version: 2 };
          return json(route, current);
        }
        if (path.endsWith('/b1')) { await gate.promise; current = { ...initial, version: 1, body: 'Latest server bundle' }; return json(route, current); }
        if (path.endsWith('/bundles')) return json(route, { items: [current, bundle('b2')] });
        throw new Error(path);
      });
      await page.goto('/scripts/education-browser/index.html?kind=bundle');
      await page.getByRole('button', { name: /게시자료 목록 \(/ }).click();
      const row = page.locator('article').first();
      if (editingOther) {
        await page.locator('article').last().getByRole('button', { name: '검토·편집' }).click();
        await page.getByRole('textbox', { name: '공통 교육 본문', exact: true }).fill('Other unsaved draft');
      }
      const label = action === 'publish' ? '명시적으로 게시' : '게시 취소';
      await row.getByRole('button', { name: label, exact: true }).click();
      await row.getByRole('alertdialog').getByRole('button', { name: action === 'publish' ? '게시' : '게시 취소', exact: true }).click();
      await expect(row.getByRole('button', { name: '검토·편집' })).toBeDisabled();
      gate.resolve();
      await expect(row.getByRole('alert')).toContainText('Latest server bundle');
      await expect(row.getByRole('button', { name: label, exact: true })).toBeDisabled();
      expect(versions).toEqual([0]);
      if (editingOther) await expect(page.getByRole('textbox', { name: '공통 교육 본문', exact: true })).toHaveValue('Other unsaved draft');
      await row.getByRole('button', { name: '최신 자료 확인 완료' }).click();
      await row.getByRole('button', { name: label, exact: true }).click();
      await row.getByRole('alertdialog').getByRole('button', { name: action === 'publish' ? '게시' : '게시 취소', exact: true }).click();
      await expect(row.getByRole('button', { name: action === 'publish' ? '게시 취소' : '명시적으로 게시', exact: true })).toBeEnabled();
      expect(versions).toEqual([0, 1]);
      if (editingOther) await expect(page.getByRole('textbox', { name: '공통 교육 본문', exact: true })).toHaveValue('Other unsaved draft');
    });
  }
}
