import { test, expect, type Page, type Route } from '@playwright/test';

const baseline = process.env.ZONE_RULES_BASELINE === '1';
const zone = (name: string) => ({
  zone_name: name,
  revision: '1'.padStart(64, '0'),
  required_ppe: ['helmet'],
  rules: ['Original'],
  risk_level: 2,
});
const worker = (id: string, zones: string[] = []) => ({
  user_id: id,
  display_name: id,
  role: 'worker',
  zone_names: zones,
  zone_name: zones[0] ?? null,
  is_active: true,
});
const deferred = () => {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
};
const card = (page: Page, name: string) =>
  page
    .locator('section')
    .filter({ has: page.getByRole('heading', { name, exact: true }) });
const json = (route: Route, value: unknown, status = 200) =>
  route.fulfill({ status, json: value });
async function open(
  page: Page,
  handler: (route: Route, path: string) => Promise<unknown>,
) {
  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://127.0.0.1:5189')
      throw new Error(`External request blocked: ${url.origin}`);
    if (url.pathname.startsWith('/api/'))
      return void (await handler(route, url.pathname));
    return route.continue();
  });
  await page.goto('/scripts/zone-rules-browser/index.html');
  await expect(card(page, 'A').getByRole('textbox')).toHaveValue('Original');
}
const initial = (route: Route, path: string) => {
  if (path === '/api/zones')
    return json(route, { items: [zone('A'), zone('B')] });
  if (path === '/api/users')
    return json(route, {
      items: [worker('w1', ['A']), worker('w2'), worker('w3')],
    });
  if (path === '/api/cameras') return json(route, { items: [] });
  throw new Error(`Unexpected request: ${route.request().method()} ${path}`);
};

test('baseline: late save response erases accepted rule, PPE and risk edits', async ({
  page,
}) => {
  test.skip(!baseline);
  const gate = deferred();
  let submitted: Record<string, unknown> | undefined;
  await open(page, async (route, path) => {
    if (route.request().method() === 'PUT') {
      submitted = route.request().postDataJSON();
      await gate.promise;
      return json(route, { ...submitted, zone_name: 'A', risk_level: 2 });
    }
    return initial(route, path);
  });
  const a = card(page, 'A');
  await a.getByRole('textbox').fill('Submitted');
  await a.getByRole('button', { name: '저장', exact: true }).click();
  await expect.poll(() => submitted?.rules).toEqual(['Submitted']);
  await a.getByRole('textbox').fill('Later accepted input');
  await a
    .getByRole('group', { name: '필수 PPE' })
    .getByRole('button')
    .first()
    .click();
  await a.getByRole('radio').last().click();
  await expect(a.getByRole('textbox')).toHaveValue('Later accepted input');
  await expect(a.getByRole('radio').last()).toHaveAttribute(
    'aria-checked',
    'true',
  );
  gate.resolve();
  await expect(a.getByRole('textbox')).toHaveValue('Submitted');
  await expect(a.getByRole('radio').last()).toHaveAttribute(
    'aria-checked',
    'false',
  );
  await expect(
    a.getByRole('group', { name: '필수 PPE' }).getByRole('button').first(),
  ).toHaveAttribute('aria-pressed', 'true');
  await expect(
    a.getByRole('button', { name: '저장', exact: true }),
  ).toBeDisabled();
  console.log(
    'BASELINE CONFIRMED: accepted later text/PPE/risk edits were erased; UI marked saved.',
  );
});

test('baseline: B completion unlocks still-pending A and member controls remain enabled', async ({
  page,
}) => {
  test.skip(!baseline);
  const gate = deferred();
  await open(page, async (route, path) => {
    if (route.request().method() === 'PUT') {
      if (path === '/api/zones/A') await gate.promise;
      return json(route, {
        ...zone(path.endsWith('/A') ? 'A' : 'B'),
        ...route.request().postDataJSON(),
      });
    }
    return initial(route, path);
  });
  const a = card(page, 'A');
  const b = card(page, 'B');
  await a.getByRole('textbox').fill('A submitted');
  await a.getByRole('button', { name: '저장', exact: true }).click();
  await expect(a.getByRole('combobox')).toBeEnabled();
  await b.getByRole('textbox').fill('B submitted');
  await b.getByRole('button', { name: '저장', exact: true }).click();
  await expect(
    b.getByRole('button', { name: '저장', exact: true }),
  ).toBeDisabled();
  await expect(
    a.getByRole('button', { name: '저장', exact: true }),
  ).toBeEnabled();
  gate.resolve();
  await expect(
    a.getByRole('button', { name: '저장', exact: true }),
  ).toBeDisabled();
  console.log(
    'BASELINE CONFIRMED: scalar busy cleared by B while A request still pending.',
  );
});

test('baseline: unawaited conflict refresh overwrites a later successful member update', async ({
  page,
}) => {
  test.skip(!baseline);
  const refresh = deferred();
  let reads = 0;
  await open(page, async (route, path) => {
    if (path === '/api/zones/A/members')
      return json(route, { detail: 'zone_members_changed' }, 409);
    if (path === '/api/zones/B/members')
      return json(route, { zone_name: 'B', user_ids: ['w2'] });
    if (path === '/api/users' && ++reads > 1) {
      await refresh.promise;
    }
    return initial(route, path);
  });
  await card(page, 'A').getByRole('combobox').selectOption('w2');
  await expect.poll(() => reads).toBe(2);
  await card(page, 'B').getByRole('combobox').selectOption('w2');
  await expect(
    card(page, 'B').getByRole('button', { name: 'w2 구역에서 제외' }),
  ).toBeVisible();
  refresh.resolve();
  await expect(
    card(page, 'B').getByRole('button', { name: 'w2 구역에서 제외' }),
  ).toHaveCount(0);
  console.log(
    'BASELINE CONFIRMED: pending full refresh replaced a later successful B membership update.',
  );
});

test('save locks conflicting controls; B completing cannot unlock pending A', async ({
  page,
}) => {
  test.skip(baseline);
  const gate = deferred();
  const seen: string[] = [];
  await open(page, async (route, path) => {
    if (route.request().method() === 'PUT') {
      seen.push(path);
      if (path === '/api/zones/A') await gate.promise;
      return json(route, {
        ...zone(path.endsWith('/A') ? 'A' : 'B'),
        ...route.request().postDataJSON(),
      });
    }
    return initial(route, path);
  });
  const a = card(page, 'A');
  const b = card(page, 'B');
  await a.getByRole('textbox').fill('Submitted');
  await a.getByRole('button', { name: '저장', exact: true }).click();
  await expect.poll(() => seen).toContain('/api/zones/A');
  await expect(a.getByRole('textbox')).toBeDisabled();
  for (const button of await a
    .getByRole('group', { name: '필수 PPE' })
    .getByRole('button')
    .all())
    await expect(button).toBeDisabled();
  for (const button of await a.getByRole('radio').all())
    await expect(button).toBeDisabled();
  await expect(a.getByRole('combobox')).toBeDisabled();
  await expect(
    a.getByRole('button', { name: 'w1 구역에서 제외' }),
  ).toBeDisabled();
  await expect(
    a.getByRole('button', { name: '삭제', exact: true }),
  ).toBeDisabled();
  await b.getByRole('textbox').fill('B submitted');
  await b.getByRole('button', { name: '저장', exact: true }).click();
  await expect(b.getByRole('textbox')).toBeEnabled();
  await expect(a.getByRole('textbox')).toBeDisabled();
  await expect(
    a.getByRole('button', { name: '저장 중…', exact: true }),
  ).toBeDisabled();
  gate.resolve();
  await expect(a.getByRole('textbox')).toBeEnabled();
  await a.getByRole('textbox').fill('Next draft');
  await expect(a.getByText('저장 안 됨', { exact: true })).toBeVisible();
});

test('failed save preserves draft and unlocks controls for retry', async ({
  page,
}) => {
  test.skip(baseline);
  await open(page, (route, path) =>
    route.request().method() === 'PUT'
      ? json(route, { detail: 'save_failed' }, 500)
      : initial(route, path),
  );
  const a = card(page, 'A');
  await a.getByRole('textbox').fill('Keep me');
  await a.getByRole('radio').last().click();
  await a.getByRole('button', { name: '저장', exact: true }).click();
  await expect(page.getByText(/A 저장 실패/)).toBeVisible();
  await expect(a.getByRole('textbox')).toHaveValue('Keep me');
  await expect(a.getByRole('textbox')).toBeEnabled();
  await expect(a.getByRole('radio').last()).toHaveAttribute(
    'aria-checked',
    'true',
  );
  await expect(
    a.getByRole('button', { name: '저장', exact: true }),
  ).toBeEnabled();
});

for (const status of [409, 500]) {
  test(`member ${status} awaits full refresh and serializes membership across zones`, async ({
    page,
  }) => {
    test.skip(baseline);
    const update = deferred();
    const refresh = deferred();
    let reads = 0;
    const writes: Record<string, unknown>[] = [];
    await open(page, async (route, path) => {
      if (path.endsWith('/members')) {
        writes.push(route.request().postDataJSON());
        if (writes.length === 1) {
          await update.promise;
          return json(
            route,
            {
              detail: status === 409 ? 'zone_members_changed' : 'lost_response',
            },
            status,
          );
        }
        return json(route, { zone_name: 'B', user_ids: ['w1'] });
      }
      if (path === '/api/users' && ++reads > 1) {
        await refresh.promise;
        return json(route, {
          items: [worker('w1', ['B']), worker('w2', ['A']), worker('w3')],
        });
      }
      return initial(route, path);
    });
    const a = card(page, 'A');
    const b = card(page, 'B');
    await a.getByRole('combobox').selectOption('w2');
    await expect(b.getByRole('combobox')).toBeDisabled();
    await expect(
      a.getByRole('button', { name: 'w1 구역에서 제외' }),
    ).toBeDisabled();
    update.resolve();
    await expect.poll(() => reads).toBe(2);
    await expect(a.getByRole('textbox')).toBeDisabled();
    await expect(b.getByRole('combobox')).toBeDisabled();
    await expect(page.getByText(/최신 명단을 다시 불러온/)).toHaveCount(0);
    expect(writes).toHaveLength(1);
    refresh.resolve();
    await expect(a.getByRole('textbox')).toBeEnabled();
    await expect(b.getByRole('combobox')).toBeEnabled();
    await expect(
      a.getByRole('button', { name: 'w2 구역에서 제외' }),
    ).toBeEnabled();
    await expect(
      b.getByRole('button', { name: 'w1 구역에서 제외' }),
    ).toBeEnabled();
    await expect(
      page.getByText(
        status === 409
          ? /다른 관리자에 의해 변경되었습니다/
          : /저장 응답을 받지 못했습니다/,
      ),
    ).toBeVisible();
    await b.getByRole('button', { name: 'w1 구역에서 제외' }).click();
    await expect.poll(() => writes.length).toBe(2);
    expect(writes[1].expected_user_ids).toEqual(['w1']);
  });
}

test('failed refresh blocks stale writes until awaited retry succeeds', async ({
  page,
}) => {
  test.skip(baseline);
  const retry = deferred();
  let reads = 0;
  let writes = 0;
  await open(page, async (route, path) => {
    if (path.endsWith('/members')) {
      writes++;
      return json(route, { detail: 'zone_members_changed' }, 409);
    }
    if (path === '/api/users') {
      reads++;
      if (reads === 2) return json(route, { detail: 'refresh_failed' }, 500);
      if (reads === 3) {
        await retry.promise;
        return json(route, {
          items: [worker('w1', ['B']), worker('w2'), worker('w3')],
        });
      }
    }
    return initial(route, path);
  });
  await card(page, 'A').getByRole('combobox').selectOption('w2');
  await expect(
    page.getByText(/최신 작업자 명단을 불러오지 못했습니다/),
  ).toBeVisible();
  await expect(card(page, 'A').getByRole('textbox')).toBeEnabled();
  await expect(card(page, 'A').getByRole('combobox')).toBeDisabled();
  await expect(card(page, 'B').getByRole('combobox')).toBeDisabled();
  const button = page.getByRole('button', {
    name: '작업자 명단 다시 불러오기',
  });
  await button.click();
  await expect(button).toBeDisabled();
  await expect(card(page, 'B').getByRole('combobox')).toBeDisabled();
  retry.resolve();
  await expect(
    page.getByText('최신 작업자 명단을 불러왔습니다.', { exact: false }),
  ).toBeVisible();
  await expect(card(page, 'B').getByRole('combobox')).toBeEnabled();
  await expect(
    card(page, 'B').getByRole('button', { name: 'w1 구역에서 제외' }),
  ).toBeEnabled();
  expect(writes).toBe(1);
});

test('initial worker read blocks member writes; failure stays blocked until retry', async ({
  page,
}) => {
  test.skip(baseline);
  const firstRead = deferred();
  let reads = 0;
  await open(page, async (route, path) => {
    if (path === '/api/users' && ++reads === 1) {
      await firstRead.promise;
      return json(route, { detail: 'initial_failed' }, 500);
    }
    return initial(route, path);
  });
  await expect(card(page, 'A').getByRole('textbox')).toBeEnabled();
  await expect(card(page, 'A').getByRole('combobox')).toHaveCount(0);
  firstRead.resolve();
  await expect(page.getByText(/작업자 명단 조회 실패/)).toBeVisible();
  await page.getByRole('button', { name: '작업자 명단 다시 불러오기' }).click();
  await expect(card(page, 'A').getByRole('combobox')).toBeEnabled();
  await expect(
    card(page, 'A').getByRole('button', { name: 'w1 구역에서 제외' }),
  ).toBeEnabled();
});

test('successful member update holds lock, preserves other zone, then allows next update', async ({
  page,
}) => {
  test.skip(baseline);
  const update = deferred();
  const writes: Record<string, unknown>[] = [];
  await open(page, async (route, path) => {
    if (path.endsWith('/members')) {
      writes.push(route.request().postDataJSON());
      if (writes.length === 1) await update.promise;
      return json(route, {
        zone_name: path.includes('/A/') ? 'A' : 'B',
        user_ids: route.request().postDataJSON().user_ids,
      });
    }
    if (path === '/api/users')
      return json(route, {
        items: [worker('w1', ['A', 'B']), worker('w2'), worker('w3')],
      });
    return initial(route, path);
  });
  await card(page, 'A').getByRole('combobox').selectOption('w2');
  await expect(card(page, 'A').getByRole('textbox')).toBeDisabled();
  await expect(card(page, 'B').getByRole('combobox')).toBeDisabled();
  await expect(card(page, 'B').getByRole('textbox')).toBeEnabled();
  update.resolve();
  await expect(
    card(page, 'A').getByRole('button', { name: 'w2 구역에서 제외' }),
  ).toBeEnabled();
  await expect(
    card(page, 'B').getByRole('button', { name: 'w1 구역에서 제외' }),
  ).toBeEnabled();
  await card(page, 'B').getByRole('combobox').selectOption('w3');
  await expect(
    card(page, 'B').getByRole('button', { name: 'w3 구역에서 제외' }),
  ).toBeEnabled();
  expect(writes[0]).toEqual({
    user_ids: ['w1', 'w2'],
    expected_user_ids: ['w1'],
  });
  expect(writes[1]).toEqual({
    user_ids: ['w1', 'w3'],
    expected_user_ids: ['w1'],
  });
});

test('pending delete locks its own draft after another zone save finishes', async ({
  page,
}) => {
  test.skip(baseline);
  const removal = deferred();
  await open(page, async (route, path) => {
    if (route.request().method() === 'DELETE') {
      await removal.promise;
      return json(route, { ok: true });
    }
    if (route.request().method() === 'PUT')
      return json(route, { ...zone('B'), ...route.request().postDataJSON() });
    return initial(route, path);
  });
  const a = card(page, 'A');
  await a.getByRole('button', { name: '삭제', exact: true }).click();
  await a
    .getByRole('alertdialog')
    .getByRole('button', { name: '삭제', exact: true })
    .click();
  await expect(a.getByRole('textbox')).toBeDisabled();
  await card(page, 'B').getByRole('textbox').fill('B saved');
  await card(page, 'B')
    .getByRole('button', { name: '저장', exact: true })
    .click();
  await expect(card(page, 'B').getByRole('textbox')).toBeEnabled();
  await expect(a.getByRole('textbox')).toBeDisabled();
  removal.resolve();
  await expect(a).toHaveCount(0);
});
