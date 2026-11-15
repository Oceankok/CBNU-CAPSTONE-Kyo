import { test, expect, type Page, type Route } from '@playwright/test';

const json = (route: Route, body: unknown, status = 200) => route.fulfill({ json: body, status });
async function setup(page: Page, handle: (route: Route, url: URL) => Promise<unknown>) {
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://127.0.0.1:5188') throw Error('External request blocked');
    if (url.pathname.startsWith('/api/')) return void await handle(route, url);
    return route.continue();
  });
}
const translation = (version = 0) => ({ translation_id: 't1', source_kind: 'material', language: 'en', source_version: 1, version,
  title: 'English title', body: 'Server body', checklist: ['Server check'], provider: 'manual', status: 'draft', stale: false });
for (const source of ['material', 'bundle']) {
  test(`manual ${source}: delayed create keeps input locked; save then explicit publication`, async ({ page }) => {
    let release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    const writes: { path: string; body: Record<string, unknown> }[] = [];
    let items: ReturnType<typeof translation>[] = [];
    await setup(page, async (route, url) => {
      if (route.request().method() === 'GET') return json(route, { items });
      writes.push({ path: url.pathname, body: route.request().postDataJSON() });
      if (url.pathname.endsWith('/translations')) { await gate; items = [translation()]; return json(route, items[0], 201); }
      items = [{ ...translation(1), status: 'published' }];
      return json(route, items[0]);
    });
    await page.goto(`/scripts/education-browser/index.html?kind=translations&source=${source}`);
    await page.getByRole('button', { name: '수동 번역 작성·게시' }).click();
    await page.getByRole('button', { name: '+ 수동 번역 작성' }).click();
    await page.getByRole('textbox', { name: '번역 제목', exact: true }).fill('My title');
    await page.getByRole('textbox', { name: '번역 본문', exact: true }).fill('My body');
    await page.getByRole('textbox', { name: '번역 확인 목록', exact: true }).fill('Check');
    await page.getByRole('button', { name: '번역 초안 저장' }).click();
    await expect(page.getByRole('textbox', { name: '번역 본문', exact: true })).toBeDisabled();
    expect(writes[0].body.expected_source_version).toBe(1);
    expect(writes[0].path).toBe(`/api/education/${source === 'material' ? 'materials' : 'bundles'}/source1/translations`);
    release();
    await expect(page.getByRole('textbox', { name: '번역 본문', exact: true })).toBeEnabled();
    await page.getByRole('button', { name: '편집 종료' }).click();
    await page.getByRole('button', { name: '번역 게시', exact: true }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: '확인' }).click();
    await expect.poll(() => writes.length).toBe(2);
    expect(writes[1].body).toEqual({ expected_version: 0, approved: true });
  });
}

test('manual 409 retains fields and selects latest version without automatic save', async ({ page }) => {
  const versions: number[] = [];
  let latest = translation();
  await setup(page, async (route) => {
    if (route.request().method() === 'GET') return json(route, { items: [latest] });
    versions.push(route.request().postDataJSON().expected_version);
    if (versions.length === 1) { latest = translation(1); return json(route, { detail: 'education_translation_conflict' }, 409); }
    return json(route, translation(2));
  });
  await page.goto('/scripts/education-browser/index.html?kind=translations');
  await page.getByRole('button', { name: '수동 번역 작성·게시' }).click();
  await page.getByRole('button', { name: '번역 수정', exact: true }).click();
  await page.getByRole('textbox', { name: '번역 본문', exact: true }).fill('My unsaved translation');
  await page.getByRole('button', { name: '번역 초안 저장' }).click();
  await expect(page.getByRole('button', { name: '번역 초안 저장' })).toBeDisabled();
  await expect(page.getByRole('textbox', { name: '번역 본문', exact: true })).toHaveValue('My unsaved translation');
  await page.getByRole('button', { name: '입력 유지·최신 번역 버전 선택' }).click();
  expect(versions).toEqual([0]);
  await page.getByRole('button', { name: '번역 초안 저장' }).click();
  await expect.poll(() => versions).toEqual([0, 1]);
});

for (const action of ['원본 버전 변경', '원본 게시 취소']) {
  test(`manual draft preserved and blocked after ${action}`, async ({ page }) => {
    await setup(page, route => json(route, { items: [translation()] }));
    await page.goto('/scripts/education-browser/index.html?kind=translations');
    await page.getByRole('button', { name: '수동 번역 작성·게시' }).click();
    await page.getByRole('button', { name: '번역 수정', exact: true }).click();
    await page.getByRole('textbox', { name: '번역 본문', exact: true }).fill('Keep draft');
    await page.getByRole('button', { name: action, exact: true }).click();
    await expect(page.getByRole('button', { name: '번역 초안 저장' })).toBeDisabled();
    await expect(page.getByRole('textbox', { name: '번역 본문', exact: true })).toHaveValue('Keep draft');
  });
}

const original = { title: '한국어 제목', body: '한국어 교육 본문', checklist: ['한국어 점검'] };
const item = (status: string, origin = 'ko', language = 'ko', requested = 'en') => ({
  recommendation_id: 'r1', ppe_type: 'helmet', zone_name: 'A', education_topic: 'Safety', material: origin === 'ko' ? original.body : 'English education',
  language, requested_language: requested, translation_fallback: language !== requested, original,
  translation: { status, origin, source_kind: 'material', source_id: 'm1', source_version: 1, available_languages: ['en'], poll_after_seconds: 3 },
});
const groups = (entry: ReturnType<typeof item>) => ({ groups: [{ quarter: '2026-Q4', zone_name: 'A', items: [entry] }] });
async function workerSetup(page: Page, get: (url: URL) => ReturnType<typeof item>, post?: () => void) {
  await setup(page, async (route, url) => {
    if (url.pathname.endsWith('/request')) { post?.(); return json(route, { status: 'pending', poll_after_seconds: 3 }, 202); }
    if (url.pathname.endsWith('/education')) return json(route, groups(get(url)));
    if (url.pathname.endsWith('/education-bundles')) return json(route, { items: [] });
    if (url.pathname.endsWith('/zones')) return json(route, { items: [{ zone_name: 'A', required_ppe: ['helmet'], rules: [] }] });
    throw Error(url.pathname);
  });
}
test('worker explicit selection wins browser preference and is persisted; Korean always selectable', async ({ page }) => {
  const queries: (string | null)[] = [];
  await workerSetup(page, url => {
    const language = url.searchParams.get('language'); queries.push(language);
    return language === 'ko' ? item('original', 'ko', 'ko', 'ko') : item('manual', 'reviewed', 'en');
  });
  await page.goto('/scripts/education-browser/index.html?kind=worker');
  await expect(page.getByText('English education')).toBeVisible();
  expect(queries[0]).toBeNull();
  await page.getByRole('combobox', { name: '교육자료 언어' }).selectOption('ko');
  await expect(page.getByText(original.body, { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('combobox', { name: '교육자료 언어' })).toHaveValue('ko');
  expect(queries.at(-1)).toBe('ko');
});
for (const terminal of ['ready', 'failed']) {
  test(`worker missing→pending→${terminal} leaves Korean readable and deduplicates requests`, async ({ page }) => {
    let reads = 0; let posts = 0;
    await workerSetup(page, () => { reads++; return reads === 1 ? item('missing') : reads === 2 ? item('pending') : terminal === 'ready' ? item('ready', 'ai', 'en') : item('failed'); }, () => { posts++; });
    await page.goto('/scripts/education-browser/index.html?kind=worker');
    await expect(page.getByText(original.body, { exact: true })).toBeVisible();
    await expect.poll(() => reads, { timeout: 12000 }).toBeGreaterThanOrEqual(3);
    if (terminal === 'ready') {
      await expect(page.getByText('AI 자동 번역', { exact: false })).toBeVisible();
      await expect(page.getByText('English education')).toBeVisible();
      await page.getByText('한국어 원문 보기', { exact: true }).click();
      await expect(page.getByText(original.body, { exact: true })).toBeVisible();
    } else {
      await expect(page.getByText(/번역을 준비하지 못했습니다/)).toBeVisible();
      await expect(page.getByText(original.body, { exact: true })).toBeVisible();
    }
    expect(posts).toBe(1);
  });
}
test('worker current manual content wins and no generation request is made', async ({ page }) => {
  let posts = 0;
  await workerSetup(page, () => item('manual', 'reviewed', 'en'), () => { posts++; });
  await page.goto('/scripts/education-browser/index.html?kind=worker');
  await expect(page.getByText('수동 번역 · en', { exact: true })).toBeVisible();
  await expect(page.getByText('English education')).toBeVisible();
  expect(posts).toBe(0);
});

test('worker old language response cannot replace explicit Korean', async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await setup(page, async (route, url) => {
    if (url.pathname.endsWith('/education')) {
      if (!url.searchParams.has('language')) { await gate; return json(route, groups(item('ready', 'ai', 'en'))); }
      return json(route, groups(item('original', 'ko', 'ko', 'ko')));
    }
    return json(route, { items: [] });
  });
  await page.goto('/scripts/education-browser/index.html?kind=worker');
  await page.getByRole('combobox', { name: '교육자료 언어' }).selectOption('ko');
  const complete = page.waitForResponse(response => response.url().endsWith('/api/worker/education'));
  release(); await complete;
  await expect(page.getByText('English education')).toHaveCount(0);
  await expect(page.getByRole('combobox', { name: '교육자료 언어' })).toHaveValue('ko');
});
test('worker refresh discards a stale translation and an unpublished source', async ({ page }) => {
  let reads = 0;
  await workerSetup(page, () => {
    reads++;
    return reads === 1 ? item('ready', 'ai', 'en') : { ...item('disabled'), material: null, original: null, translation: null };
  });
  await page.goto('/scripts/education-browser/index.html?kind=worker');
  await expect(page.getByText('English education')).toBeVisible();
  await page.getByRole('button', { name: '교육자료 새로 조회' }).click();
  await expect(page.getByText('English education')).toHaveCount(0);
  await expect(page.getByText('한국어 원문 보기')).toHaveCount(0);
});
for (const status of ['disabled', 'ineligible', 'failed']) {
  test(`worker ${status} never starts automatic translation`, async ({ page }) => {
    let posts = 0;
    await workerSetup(page, () => item(status), () => { posts++; });
    await page.goto('/scripts/education-browser/index.html?kind=worker');
    await expect(page.getByText(original.body, { exact: true })).toBeVisible();
    await page.getByRole('button', { name: '교육자료 새로 조회' }).click();
    await expect(page.getByText(original.body, { exact: true })).toBeVisible();
    expect(posts).toBe(0);
  });
}
test('manual publication conflict refreshes review without automatically publishing again', async ({ page }) => {
  let writes = 0;
  let latest = translation();
  await setup(page, async route => {
    if (route.request().method() === 'GET') return json(route, { items: [latest] });
    writes++; latest = translation(1); return json(route, { detail: 'education_translation_conflict' }, 409);
  });
  await page.goto('/scripts/education-browser/index.html?kind=translations');
  await page.getByRole('button', { name: '수동 번역 작성·게시' }).click();
  await page.getByRole('button', { name: '번역 게시', exact: true }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: '확인' }).click();
  await expect(page.getByRole('button', { name: '번역 게시', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '최신 번역 확인 완료' }).click();
  expect(writes).toBe(1);
  await expect(page.getByRole('button', { name: '번역 게시', exact: true })).toBeEnabled();
});

for (const source of ['material', 'bundle']) {
  test(`admin copies current ${source} AI result into a new manual draft, not an auto job edit`, async ({ page }) => {
    const writes: { method: string; path: string; body: Record<string, unknown> }[] = [];
    await setup(page, async (route, url) => {
      if (url.pathname.endsWith('/automatic-translations')) return json(route, { items: [{ source_kind: source, source_id: 'source1', source_version: 1, language: 'en', status: 'ready', origin: 'ai', content: { title: 'Auto title', body: 'Auto body', checklist: ['Auto check'] } }] });
      if (route.request().method() === 'GET') return json(route, { items: [] });
      writes.push({ method: route.request().method(), path: url.pathname, body: route.request().postDataJSON() });
      return json(route, translation());
    });
    await page.goto(`/scripts/education-browser/index.html?kind=translations&source=${source}`);
    await page.getByRole('button', { name: '수동 번역 작성·게시' }).click();
    await page.getByRole('button', { name: '자동 번역을 수동 초안으로 복사 (en)' }).click();
    await expect(page.getByRole('textbox', { name: '번역 본문', exact: true })).toHaveValue('Auto body');
    await page.getByRole('textbox', { name: '번역 본문', exact: true }).fill('Corrected by administrator');
    await page.getByRole('button', { name: '번역 초안 저장' }).click();
    await expect.poll(() => writes.length).toBe(1);
    expect(writes[0]).toMatchObject({ method: 'POST', path: `/api/education/${source === 'material' ? 'materials' : 'bundles'}/source1/translations`, body: { body: 'Corrected by administrator', expected_source_version: 1, language: 'en' } });
  });
}
test('admin existing reviewed AI-authored draft remains editable', async ({ page }) => {
  await setup(page, (route, url) => json(route, { items: url.pathname.endsWith('/automatic-translations') ? [] : [{ ...translation(), provider: 'openai' }] }));
  await page.goto('/scripts/education-browser/index.html?kind=translations');
  await page.getByRole('button', { name: '수동 번역 작성·게시' }).click();
  await expect(page.getByRole('button', { name: '번역 수정', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '번역 수정', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '번역 본문', exact: true })).toHaveValue('Server body');
});
test('worker regional fallback displays requested and actual locale with manual provenance', async ({ page }) => {
  await workerSetup(page, () => item('manual', 'reviewed', 'en', 'en-US'));
  await page.goto('/scripts/education-browser/index.html?kind=worker');
  await expect(page.getByText('수동 번역 · en · 요청 언어 en-US', { exact: true })).toBeVisible();
  await expect(page.getByText('AI 자동 번역', { exact: false })).toHaveCount(0);
});
test('worker bundle shows existing automatic content and its Korean original', async ({ page }) => {
  await setup(page, async (route, url) => {
    if (url.pathname.endsWith('/zones')) return json(route, { items: [] });
    if (url.pathname.endsWith('/education')) return json(route, { groups: [] });
    if (url.pathname.endsWith('/education-bundles')) return json(route, { items: [{ ...item('ready', 'ai', 'en'), bundle_id: 'b1', quarter: '2026-Q4', assigned_zones: ['A'], title: 'English bundle', body: 'Translated bundle', checklist: ['Translated check'] }] });
    throw Error(url.pathname);
  });
  await page.goto('/scripts/education-browser/index.html?kind=worker');
  await expect(page.getByText('Translated bundle', { exact: true })).toBeVisible();
  await page.getByText('한국어 원문 보기', { exact: true }).click();
  await expect(page.getByText(original.body, { exact: true })).toBeVisible();
});

async function startManual(page: Page) {
  await page.goto('/scripts/education-browser/index.html?kind=translations');
  await page.getByRole('button', { name: '수동 번역 작성·게시' }).click();
  await page.getByRole('button', { name: '+ 수동 번역 작성' }).click();
  await page.getByRole('textbox', { name: '번역 제목', exact: true }).fill('Title');
  await page.getByRole('textbox', { name: '번역 본문', exact: true }).fill('Preserved manual input');
  await page.getByRole('textbox', { name: '번역 확인 목록', exact: true }).fill('Check');
}
test('delayed initial translation list cannot overwrite post-create refresh; same-tick create is locked', async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let reads = 0; let writes = 0;
  await setup(page, async (route, url) => {
    if (url.pathname.endsWith('/automatic-translations')) return json(route, { items: [] });
    if (route.request().method() === 'GET') {
      reads++; if (reads === 1) { await gate; return json(route, { items: [] }); }
      return json(route, { items: [translation()] });
    }
    writes++; return json(route, translation(), 201);
  });
  await startManual(page);
  await page.getByRole('button', { name: '번역 초안 저장' }).evaluate((button: HTMLButtonElement) => { button.click(); button.click(); });
  await expect(page.locator('article')).toHaveCount(1);
  const response = page.waitForResponse(r => r.request().method() === 'GET' && r.url().includes('/translations?'));
  release(); await (await response).finished();
  await expect(page.locator('article')).toHaveCount(1);
  expect(writes).toBe(1);
});
for (const initiallyPublished of [true, false]) {
  test(`source409 fetches latest Korean and preserves input through recovery, published=${initiallyPublished}`, async ({ page }) => {
    let published = initiallyPublished;
    const versions: number[] = [];
    await setup(page, async (route, url) => {
      if (url.pathname.endsWith('/source1')) return json(route, { ...original, version: 2, status: published ? 'published' : 'draft' });
      if (route.request().method() === 'GET') return json(route, { items: [] });
      versions.push(route.request().postDataJSON().expected_source_version);
      return versions.length === 1 ? json(route, { detail: published ? 'education_translation_source_changed' : 'education_translation_source_not_published' }, 409) : json(route, { ...translation(), source_version: 2 }, 201);
    });
    await startManual(page);
    await page.getByRole('button', { name: '번역 초안 저장' }).click();
    await expect(page.getByText('최신 한국어 원본 v2', { exact: false })).toBeVisible();
    if (!published) {
      await expect(page.getByRole('button', { name: '입력 유지·최신 원본으로 새 번역 선택' })).toHaveCount(0);
      published = true;
      await page.getByRole('button', { name: '최신 한국어 원본 조회' }).click();
    }
    await page.getByRole('button', { name: '입력 유지·최신 원본으로 새 번역 선택' }).click();
    await expect(page.getByRole('textbox', { name: '번역 본문', exact: true })).toHaveValue('Preserved manual input');
    expect(versions).toEqual([1]);
    await page.getByRole('button', { name: '번역 초안 저장' }).click();
    await expect.poll(() => versions).toEqual([1, 2]);
  });
}
test('active-page freshness removes ready translated content after remote unpublish without manual reload', async ({ page }) => {
  await page.clock.install();
  let available = true;
  await workerSetup(page, () => available ? item('ready', 'ai', 'en') : { ...item('disabled'), material: null, translation: null, original: null });
  await page.goto('/scripts/education-browser/index.html?kind=worker');
  await expect(page.getByText('English education')).toBeVisible();
  available = false;
  await page.clock.runFor(31000);
  await expect(page.getByText('English education')).toHaveCount(0);
});
test('missing enqueue transport failure offers explicit retry; lost response resumes pending without reenqueue', async ({ page }) => {
  await page.clock.install();
  let posts = 0; let status = 'missing';
  await setup(page, async (route, url) => {
    if (url.pathname.endsWith('/request')) {
      posts++;
      if (posts === 2) status = 'pending';
      return route.abort('failed');
    }
    if (url.pathname.endsWith('/education')) return json(route, groups(item(status)));
    if (url.pathname.endsWith('/zones')) return json(route, { items: [{ zone_name: 'A', required_ppe: [], rules: [] }] });
    return json(route, { items: [] });
  });
  await page.goto('/scripts/education-browser/index.html?kind=worker');
  await expect(page.getByRole('button', { name: '실패한 번역 요청 다시 시도' })).toBeVisible();
  await page.clock.runFor(4000);
  expect(posts).toBe(1);
  await page.getByRole('button', { name: '실패한 번역 요청 다시 시도' }).click();
  await expect.poll(() => posts).toBe(2);
  await page.clock.runFor(4000);
  await expect(page.getByText(/번역 준비 중입니다/)).toBeVisible();
  await expect(page.getByRole('button', { name: '실패한 번역 요청 다시 시도' })).toHaveCount(0);
  expect(posts).toBe(2);
});

test('translated content expires if freshness GET stalls', async ({ page }) => {
  await page.clock.install();
  let reads = 0;
  await setup(page, async (route, url) => {
    if (url.pathname.endsWith('/education')) {
      reads++;
      if (reads > 1) return; // Leave routed request unresolved to exercise the client bound.
      return json(route, groups(item('ready', 'ai', 'en')));
    }
    if (url.pathname.endsWith('/zones')) return json(route, { items: [{ zone_name: 'A', required_ppe: [], rules: [] }] });
    return json(route, { items: [] });
  });
  await page.goto('/scripts/education-browser/index.html?kind=worker');
  await expect(page.getByText('English education')).toBeVisible();
  await page.clock.runFor(41000);
  await expect(page.getByText('English education')).toHaveCount(0);
  await expect(page.getByText(/최신 게시 상태를 확인하지 못해/)).toBeVisible();
});
test('source revision changes invalidate delayed translation list response', async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let reads = 0;
  await setup(page, async (route, url) => {
    if (url.pathname.endsWith('/automatic-translations')) return json(route, { items: [] });
    reads++;
    if (reads === 1) { await gate; return json(route, { items: [translation()] }); }
    return json(route, { items: [{ ...translation(2), title: 'Current revision', source_version: 2 }] });
  });
  await page.goto('/scripts/education-browser/index.html?kind=translations');
  await page.getByRole('button', { name: '수동 번역 작성·게시' }).click();
  await page.getByRole('button', { name: '원본 버전 변경' }).click();
  await expect(page.locator('article')).toContainText('Current revision');
  const complete = page.waitForResponse(r => r.url().includes('/translations?'));
  release(); await (await complete).finished();
  await expect(page.locator('article')).toContainText('Current revision');
});

test('worker language options use native names and admin job status uses readable Korean', async ({ page }) => {
  await workerSetup(page, () => item('manual', 'reviewed', 'en'));
  await page.goto('/scripts/education-browser/index.html?kind=worker');
  await expect(page.getByRole('option', { name: 'English', exact: true })).toHaveCount(1);
  await expect(page.getByRole('option', { name: 'Tiếng Việt', exact: true })).toHaveCount(1);
  await page.unroute('**/*');
  await setup(page, (route, url) => json(route, { items: url.pathname.endsWith('/automatic-translations') ? [{ source_kind: 'material', source_id: 'source1', source_version: 1, language: 'en', status: 'unknown', origin: 'ai', content: null }] : [] }));
  await page.goto('/scripts/education-browser/index.html?kind=translations');
  await page.getByRole('button', { name: '수동 번역 작성·게시' }).click();
  await expect(page.getByText('AI 자동 번역 · en · 번역 결과 확인 불가', { exact: true })).toBeVisible();
});
