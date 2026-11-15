import { test, expect, type Page, type Route } from '@playwright/test';
const policy = (version = 1, mode = 'shadow') => ({ version, mode, decision_method: 'continuous_rules_v1', rule_criteria: { minimum_observed_seconds: 5, minimum_frame_samples: 10 }, threshold: 0.9, model_versions: ['model'], ppe_types: ['helmet'], camera_ids: ['C1'], zone_names: [], validation_reference: null, activation_blockers: ['approved_validation_reference_required'], automatic_confirmation_enabled: mode === 'enforce' });
const rule = { method: 'continuous_rules_v1', eligible: false, criteria: policy().rule_criteria, observed_seconds: 4, frame_sample_count: 8, gap_count: 1, gap_seconds: 0.3, close_reason: 'observation_gap', score: null, blocking_reasons: ['continuous_observation_incomplete'], limitations: ['producer_absence_heuristic_not_independent_ppe_verification'] };
const diagnostic = { event_id: 'E1', observed_confidence: null, threshold_candidate: false, blocking_reasons: rule.blocking_reasons, evidence_fingerprint: 'fingerprint', decision_method: 'continuous_rules_v1', rule_evaluation: rule };
const audit = { source: 'automatic_continuous_rules_v1', settings_version: 4, policy_configured_by: 'admin', policy: policy(), model_version: 'model', evidence_fingerprint: 'fingerprint', rule_evaluation: rule };
const json = (route: Route, value: unknown, status = 200) => route.fulfill({ json: value, status });
const gate = () => { let resolve!: () => void; const promise = new Promise<void>(done => { resolve = done; }); return { promise, resolve }; };
async function setup(page: Page, handler?: (route: Route, url: URL) => Promise<unknown>) {
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://127.0.0.1:5192') throw Error('External origin blocked');
    if (!url.pathname.startsWith('/api/')) return route.continue();
    if (handler) return void await handler(route, url);
    return json(route, policy());
  });
}
const open = (page: Page, query = '') => page.goto('/scripts/auto-rules-browser/index.html' + query);
const reason = (page: Page) => page.getByRole('textbox', { name: '변경 사유', exact: true });
const save = (page: Page) => page.getByRole('button', { name: 'OFF·shadow 설정 저장', exact: true });

test('rule settings round-trip both explicit criteria; saving locks fields and sends no activation fields', async ({ page }) => {
  const deferred = gate(); let payload: Record<string, unknown> | undefined;
  await setup(page, async route => {
    if (route.request().method() === 'PUT') { payload = route.request().postDataJSON(); await deferred.promise; return json(route, policy(2)); }
    return json(route, policy());
  });
  await open(page);
  await page.getByRole('spinbutton', { name: /최소 실제 관측 시간/ }).fill('7');
  await page.getByRole('spinbutton', { name: /최소 프레임 샘플/ }).fill('14');
  await reason(page).fill('shadow test');
  await save(page).evaluate((button: HTMLButtonElement) => { button.click(); button.click(); });
  await expect(reason(page)).toBeDisabled();
  await expect(page.getByRole('spinbutton', { name: /최소 실제 관측 시간/ })).toBeDisabled();
  await expect.poll(() => payload).toMatchObject({ expected_version: 1, mode: 'shadow', decision_method: 'continuous_rules_v1', rule_criteria: { minimum_observed_seconds: 7, minimum_frame_samples: 14 }, threshold: 0.9 });
  expect(payload).not.toHaveProperty('activation_blockers');
  deferred.resolve(); await expect(reason(page)).toHaveValue('');
});
test('version409 preserves draft and retries only after explicit latest-version adoption', async ({ page }) => {
  let server = policy(); const versions: number[] = [];
  await setup(page, async route => {
    if (route.request().method() === 'PUT') {
      versions.push(route.request().postDataJSON().expected_version);
      if (versions.length === 1) { server = policy(2); return json(route, { detail: 'auto_decision_settings_changed' }, 409); }
      return json(route, policy(3));
    }
    return json(route, server);
  });
  await open(page); await reason(page).fill('Preserved reason');
  await page.getByRole('spinbutton', { name: /최소 실제 관측 시간/ }).fill('12');
  await save(page).click();
  await expect(page.getByRole('region', { name: '최신 서버 설정' })).toContainText('v2');
  await expect(save(page)).toBeDisabled(); await expect(reason(page)).toHaveValue('Preserved reason');
  expect(versions).toEqual([1]);
  await page.getByRole('button', { name: '입력 유지·최신 버전 선택' }).click();
  await expect(page.getByRole('spinbutton', { name: /최소 실제 관측 시간/ })).toHaveValue('12');
  await save(page).click(); await expect.poll(() => versions).toEqual([1, 2]);
});
test('failed latest fetch stays recoverable and latest-server selection explicitly replaces the draft', async ({ page }) => {
  let reads = 0;
  await setup(page, async route => {
    if (route.request().method() === 'PUT') return json(route, { detail: 'auto_decision_settings_changed' }, 409);
    reads++; return reads === 2 ? json(route, { detail: 'offline' }, 503) : json(route, policy(reads > 2 ? 3 : 1));
  });
  await open(page); await reason(page).fill('Keep until choice'); await save(page).click();
  await expect(page.getByText(/최신 설정 조회에 실패/)).toBeVisible();
  await expect(reason(page)).toHaveValue('Keep until choice'); await expect(save(page)).toBeDisabled();
  await page.getByRole('button', { name: '최신 설정 조회', exact: true }).click();
  await page.getByRole('button', { name: '입력 취소·최신 설정으로 시작' }).click();
  await expect(reason(page)).toHaveValue(''); await expect(page.getByText('정책 입력 · 기준 버전 3')).toBeVisible();
});
test('current preview uses edited method and criteria and is separate from saved blockers and history', async ({ page }) => {
  let body: Record<string, unknown> | undefined;
  await setup(page, async (route, url) => {
    if (url.pathname.endsWith('/preview')) { body = route.request().postDataJSON(); return json(route, { persisted: false, automatic_confirmation_enabled: false, items: [diagnostic] }); }
    return json(route, policy());
  });
  await open(page); await page.getByRole('textbox', { name: '이벤트 ID (1~20개, 한 줄에 하나)' }).fill('E1');
  await page.getByRole('spinbutton', { name: /최소 실제 관측 시간/ }).fill('6');
  await page.getByRole('button', { name: '현재 입력 읽기 전용 진단' }).click();
  await expect(page.getByRole('region', { name: '현재 입력 진단' })).toContainText('확률 점수 없음');
  await expect(page.getByRole('region', { name: '현재 입력 진단' })).toContainText('continuous_observation_incomplete');
  expect(body).toMatchObject({ decision_method: 'continuous_rules_v1', rule_criteria: { minimum_observed_seconds: 6, minimum_frame_samples: 10 } });
  expect(body).not.toHaveProperty('expected_version');
  await page.getByRole('spinbutton', { name: /최소 프레임 샘플/ }).fill('11');
  await expect(page.getByRole('region', { name: '현재 입력 진단' }).locator('article')).toHaveCount(0);
  await expect(page.getByRole('region', { name: '저장된 설정의 활성화 차단 사유' })).toContainText('approved_validation_reference_required');
});
test('saved enforce is display-only and must be changed to OFF before any PUT or preview', async ({ page }) => {
  let payload: Record<string, unknown> | undefined;
  await setup(page, async route => {
    if (route.request().method() === 'PUT') { payload = route.request().postDataJSON(); return json(route, policy(2, 'off')); }
    return json(route, { ...policy(1, 'enforce'), activation_blockers: [] });
  });
  await open(page); await reason(page).fill('disable'); await expect(save(page)).toBeDisabled();
  await expect(page.getByRole('option', { name: /enforce/ })).toBeDisabled();
  await page.getByRole('combobox', { name: '모드', exact: true }).selectOption('off'); await save(page).click();
  await expect.poll(() => payload?.mode).toBe('off');
});
test('rule method requires explicit criteria for shadow and rejects fractional samples', async ({ page }) => {
  await setup(page, route => json(route, { ...policy(), decision_method: 'validated_model', rule_criteria: null }));
  await open(page); await reason(page).fill('scope');
  await page.getByRole('combobox', { name: '판단 방법' }).selectOption('continuous_rules_v1');
  await expect(page.getByRole('spinbutton', { name: /최소 실제 관측 시간/ })).toHaveValue('');
  await expect(save(page)).toBeDisabled();
  await page.getByRole('spinbutton', { name: /최소 실제 관측 시간/ }).fill('3');
  await page.getByRole('spinbutton', { name: /최소 프레임 샘플/ }).fill('2.5'); await expect(save(page)).toBeDisabled();
  await page.getByRole('spinbutton', { name: /최소 프레임 샘플/ }).fill('2'); await expect(save(page)).toBeEnabled();
});
test('persisted shadow shows historical version/method/evidence/exclusions and tolerates old diagnostics', async ({ page }) => {
  const deferred = gate();
  await setup(page, async (route, url) => {
    if (url.pathname.endsWith('/shadow')) { await deferred.promise; return json(route, { event_id: 'E1', limit: 20, offset: 0, items: [
      { run_id: 'r1', actor_id: 'admin', created_at: '2026-11-15', policy: policy(), result: { ...diagnostic, settings_version: 4 } },
      { run_id: 'r2', actor_id: 'admin', created_at: '2026-11-14', policy: { threshold: 0.9 }, result: { ...diagnostic, rule_evaluation: undefined, settings_version: undefined } },
    ] }); }
    return json(route, policy());
  });
  await open(page); await page.getByRole('textbox', { name: 'shadow 조회 이벤트 ID' }).fill('E1');
  await page.getByRole('button', { name: '저장된 shadow 조회' }).click();
  await expect(page.getByRole('textbox', { name: 'shadow 조회 이벤트 ID' })).toBeDisabled(); deferred.resolve();
  const section = page.getByRole('region', { name: '저장된 shadow 이력' });
  await expect(section).toContainText('정책 버전 4'); await expect(section).toContainText('이전/수동 진단');
  await expect(section).toContainText('fingerprint'); await expect(section).toContainText('continuous_observation_incomplete');
});
test('recognized system audit is structured; malformed/manual/older comments remain safe text', async ({ page }) => {
  await setup(page); await open(page, '?kind=audit');
  await page.getByRole('textbox', { name: '감사 입력' }).fill(JSON.stringify(audit));
  await expect(page.getByRole('region', { name: '시스템 규칙 판단 근거' })).toContainText('정책 버전 4');
  await expect(page.getByRole('region', { name: '수동 코멘트 대조' }).getByRole('region')).toHaveCount(0);
  for (const text of ['수동 정정 코멘트', '{"source":"automatic_decision_v1"}', JSON.stringify({ ...audit, rule_evaluation: { ...rule, eligible: 'yes' } }), '<img src=x onerror=alert(1)>']) {
    await page.getByRole('textbox', { name: '감사 입력' }).fill(text);
    await expect(page.getByRole('region', { name: '시스템 규칙 판단 근거' })).toHaveCount(0);
    await expect(page.locator('img')).toHaveCount(0);
  }
});
test('actual review detail preserves manual correction after a system rule review', async ({ page }) => {
  let submitted: Record<string, unknown> | undefined;
  await setup(page, async (route, url) => {
    if (route.request().method() === 'PUT') { submitted = route.request().postDataJSON(); return json(route, submitted); }
    if (url.pathname === '/api/events/E1') return json(route, { event: { event_id: 'E1', event_status: 'confirmed', camera_id: 'C1', zone_name: 'A', ppe_type: 'helmet', timestamp_start: '2026-11-15T00:00:00Z', timestamp_end: '2026-11-15T00:00:05Z', duration_sec: 5, ai_confidence: null, review_only: true, model_version: 'model' }, review: submitted ? { review_id: 'MANUAL_1', event_id: 'E1', ...submitted, reviewer_id: 'admin', review_time: '2026-11-15' } : { review_id: 'AUTO_1', event_id: 'E1', reviewer_id: null, review_result: 'confirmed', review_reason_code: 'automatic_policy_confirmed', review_comment: JSON.stringify(audit), review_time: '2026-11-15', second_review_needed: true }, review_history: submitted ? [{ history_id: 'H1', review_id: 'AUTO_1', reviewer_id: null, review_reason_code: 'automatic_policy_confirmed', review_comment: JSON.stringify(audit), review_time: '2026-11-15' }] : [] });
    if (url.pathname.endsWith('/media')) return json(route, { items: [], clip_requests: [], review_availability: { state: 'available', can_review: true, can_mark_unreviewable: false, missing_media_count: 0 } });
    return json(route, { items: [], candidates: [], saved_relations: [] });
  });
  await open(page, '?kind=review');
  await expect(page.getByRole('region', { name: '시스템 규칙 판단 근거' })).toBeVisible();
  await page.getByRole('button', { name: /재검토 시작/ }).click();
  await expect(page.getByRole('combobox')).toHaveValue('');
  await expect(page.getByRole('textbox')).toContainText(JSON.stringify(audit));
  await page.getByRole('button', { name: /오탐/ }).click();
  await page.getByRole('combobox').selectOption('false_positive_other');
  await page.getByRole('textbox').fill('관리자 영상 검토 후 정정');
  await page.getByRole('button', { name: '검토 제출', exact: true }).click();
  await expect.poll(() => submitted?.review_result).toBe('false_positive');
  expect(submitted?.review_reason_code).toBe('false_positive_other');
  expect(submitted?.review_comment).toBe('관리자 영상 검토 후 정정');
  await page.getByText('이전 검토 기록 (1건)', { exact: true }).click();
  await expect(page.getByRole('region', { name: '시스템 규칙 판단 근거' })).toBeVisible();
});

test('late preview from an unmounted settings page cannot overwrite the next draft', async ({ page }) => {
  const deferred = gate(); let reads = 0;
  await setup(page, async (route, url) => {
    if (url.pathname.endsWith('/preview')) { await deferred.promise; return json(route, { items: [diagnostic], persisted: false }); }
    reads++; return json(route, policy(reads));
  });
  await open(page); await page.getByRole('textbox', { name: '이벤트 ID (1~20개, 한 줄에 하나)' }).fill('E1');
  await page.getByRole('button', { name: '현재 입력 읽기 전용 진단' }).click();
  await expect(reason(page)).toBeDisabled();
  await page.getByRole('button', { name: '설정 화면 전환' }).click();
  await page.getByRole('button', { name: '설정 화면 전환' }).click();
  await reason(page).fill('New draft');
  const complete = page.waitForResponse(response => response.url().endsWith('/preview'));
  deferred.resolve(); await (await complete).finished();
  await expect(reason(page)).toHaveValue('New draft');
  await expect(page.getByRole('region', { name: '현재 입력 진단' }).locator('article')).toHaveCount(0);
});
test('non-version save rejection preserves fields without creating a version-conflict retry loop', async ({ page }) => {
  await setup(page, route => route.request().method() === 'PUT' ? json(route, { detail: 'offline' }, 503) : json(route, policy()));
  await open(page); await reason(page).fill('Keep input'); await save(page).click();
  await expect(page.getByText(/저장하지 못했습니다/)).toBeVisible();
  await expect(reason(page)).toHaveValue('Keep input'); await expect(save(page)).toBeEnabled();
  await expect(page.getByRole('button', { name: '최신 설정 조회', exact: true })).toHaveCount(0);
});

test('incomplete persisted rule row tolerates missing observations and retains exclusions', async ({ page }) => {
  await setup(page, async (route, url) => url.pathname.endsWith('/shadow') ? json(route, { event_id: 'E1', limit: 20, offset: 0, items: [{ run_id: 'incomplete', actor_id: 'admin', created_at: '2026-11-15', policy: policy(), result: { event_id: 'E1', settings_version: 2, decision_method: 'continuous_rules_v1', blocking_reasons: ['evidence_incomplete'], rule_evaluation: { method: 'continuous_rules_v1', eligible: false, score: null } } }] }) : json(route, policy()));
  await open(page); await page.getByRole('textbox', { name: 'shadow 조회 이벤트 ID' }).fill('E1');
  await page.getByRole('button', { name: '저장된 shadow 조회' }).click();
  const section = page.getByRole('region', { name: '저장된 shadow 이력' });
  await expect(section).toContainText('evidence_incomplete');
  await expect(section).toContainText('미확인');
  await expect(section).toContainText('규칙 조건 미충족');
});
