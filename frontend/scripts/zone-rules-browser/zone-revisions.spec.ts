import { test, expect, type Page } from '@playwright/test';

const token = (n: number) => n.toString(16).padStart(64, '0');
const zone = (name: string) => ({
  zone_name: name,
  revision: token(1),
  required_ppe: ['helmet'],
  rules: ['Original'],
  risk_level: 2 as number | null,
});
const card = (page: Page, name = 'A') =>
  page
    .locator('section')
    .filter({ has: page.getByRole('heading', { name, exact: true }) });
const gate = () => {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
};
function server() {
  return {
    zones: new Map([
      ['A', zone('A')],
      ['B', zone('B')],
    ]),
    sequence: 1,
    writes: [] as {
      method: string;
      expected: unknown;
      body: Record<string, unknown>;
      status: number;
    }[],
    failReads: false,
    readGate: null as Promise<void> | null,
    refreshes: 0,
  };
}
async function mount(page: Page, state: ReturnType<typeof server>) {
  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://127.0.0.1:5191')
      throw new Error(`External request blocked: ${url.origin}`);
    if (!url.pathname.startsWith('/api/')) return route.continue();
    const method = route.request().method();
    if (url.pathname === '/api/zones') {
      state.refreshes++;
      if (state.readGate) await state.readGate;
      return state.failReads
        ? route.fulfill({ status: 500, json: { detail: 'refresh_failed' } })
        : route.fulfill({ json: { items: [...state.zones.values()] } });
    }
    if (url.pathname === '/api/users' || url.pathname === '/api/cameras')
      return route.fulfill({ json: { items: [] } });
    const name = decodeURIComponent(url.pathname.slice('/api/zones/'.length));
    const current = state.zones.get(name);
    const body = method === 'PUT' ? route.request().postDataJSON() : {};
    const expected =
      method === 'PUT'
        ? body.expected_revision
        : url.searchParams.get('expected_revision');
    const matches = current
      ? expected === current.revision
      : method === 'PUT' && expected === null;
    state.writes.push({ method, expected, body, status: matches ? 200 : 409 });
    if (!matches)
      return route.fulfill({
        status: 409,
        json: { detail: 'zone_rule_changed' },
      });
    if (method === 'DELETE') {
      state.zones.delete(name);
      return route.fulfill({ json: { status: 'deleted', zone_name: name } });
    }
    const saved = {
      zone_name: name,
      revision: token(++state.sequence),
      required_ppe: body.required_ppe,
      rules: body.rules,
      risk_level:
        'risk_level' in body ? body.risk_level : (current?.risk_level ?? null),
    };
    state.zones.set(name, saved);
    return route.fulfill({ json: saved });
  });
  await page.goto('/scripts/zone-rules-browser/index.html');
  await expect(card(page).getByRole('textbox')).toHaveValue('Original');
}

test('independent editors preserve stale input and other-zone draft until explicit resolution', async ({
  browser,
}) => {
  const state = server();
  const firstContext = await browser.newContext();
  const secondContext = await browser.newContext();
  try {
    const first = await firstContext.newPage();
    const second = await secondContext.newPage();
    await mount(first, state);
    await mount(second, state);
    await card(second).getByRole('textbox').fill('My local B input');
    await card(second).getByRole('radio').last().click();
    await card(second, 'B')
      .getByRole('textbox')
      .fill('Other-zone unsaved draft');
    await card(first).getByRole('textbox').fill('Server from first admin');
    await card(first).getByRole('radio').nth(2).click();
    await card(first)
      .getByRole('button', { name: '저장', exact: true })
      .click();
    await expect(card(first).getByRole('textbox')).toBeEnabled();
    await card(second)
      .getByRole('button', { name: '저장', exact: true })
      .click();
    await expect(card(second).getByRole('alert')).toContainText(
      'Server from first admin',
    );
    await expect(card(second).getByRole('textbox')).toHaveValue(
      'My local B input',
    );
    await expect(card(second).getByRole('radio').last()).toHaveAttribute(
      'aria-checked',
      'true',
    );
    await expect(card(second, 'B').getByRole('textbox')).toHaveValue(
      'Other-zone unsaved draft',
    );
    await expect(
      card(second).getByRole('button', { name: '저장', exact: true }),
    ).toBeDisabled();
    expect(state.writes.map((w) => w.status)).toEqual([200, 409]);
    await card(second)
      .getByRole('button', { name: '내 입력 유지하고 다시 저장 준비' })
      .click();
    expect(state.writes).toHaveLength(2);
    await card(second)
      .getByRole('button', { name: '저장', exact: true })
      .click();
    await expect(card(second).getByRole('textbox')).toBeEnabled();
    expect(state.writes[2].expected).toBe(token(2));
    expect(state.zones.get('A')?.rules).toEqual(['My local B input']);
    expect(state.zones.get('A')?.risk_level).toBe(5);
    await expect(card(second, 'B').getByRole('textbox')).toHaveValue(
      'Other-zone unsaved draft',
    );
    // Another save after resolution can make the first editor stale again.
    await card(first).getByRole('textbox').fill('First editor now stale');
    await card(first)
      .getByRole('button', { name: '저장', exact: true })
      .click();
    await expect(card(first).getByRole('alert')).toContainText(
      'My local B input',
    );
  } finally {
    await firstContext.close();
    await secondContext.close();
  }
});

test('choosing latest replaces only the conflicted draft including PPE and risk', async ({
  page,
}) => {
  const state = server();
  await mount(page, state);
  await card(page)
    .getByRole('textbox')
    .fill('Discard only after explicit choice');
  await card(page, 'B').getByRole('textbox').fill('Keep other draft');
  state.zones.set('A', {
    ...zone('A'),
    revision: token(++state.sequence),
    required_ppe: ['vest'],
    rules: ['Latest rules'],
    risk_level: 5,
  });
  await card(page).getByRole('button', { name: '저장', exact: true }).click();
  await expect(card(page).getByRole('alert')).toContainText(
    '서버 최신 필수 PPE: 안전조끼',
  );
  await card(page)
    .getByRole('button', { name: '최신 내용으로 바꾸기' })
    .click();
  await expect(card(page).getByRole('textbox')).toHaveValue('Latest rules');
  await expect(card(page).getByRole('radio').last()).toHaveAttribute(
    'aria-checked',
    'true',
  );
  await expect(
    card(page)
      .getByRole('group', { name: '필수 PPE' })
      .getByRole('button')
      .nth(1),
  ).toHaveAttribute('aria-pressed', 'true');
  await expect(
    card(page).getByRole('button', { name: '저장', exact: true }),
  ).toBeDisabled();
  await expect(card(page, 'B').getByRole('textbox')).toHaveValue(
    'Keep other draft',
  );
  expect(state.writes).toHaveLength(1);
});

for (const risk of [null, 5]) {
  test(`failed refresh preserves edits and blocks saves; retry preserves omitted risk ${risk}`, async ({
    page,
  }) => {
    const state = server();
    await mount(page, state);
    await card(page).getByRole('textbox').fill('Keep after failed refresh');
    state.zones.set('A', {
      ...zone('A'),
      revision: token(++state.sequence),
      rules: ['Latest'],
      risk_level: risk,
    });
    state.failReads = true;
    await card(page).getByRole('button', { name: '저장', exact: true }).click();
    await expect(card(page).getByRole('alert')).toContainText(
      '불러오지 못했습니다',
    );
    await expect(card(page).getByRole('textbox')).toHaveValue(
      'Keep after failed refresh',
    );
    await expect(
      card(page).getByRole('button', { name: '저장', exact: true }),
    ).toBeDisabled();
    await expect(
      card(page).getByRole('button', { name: '삭제', exact: true }),
    ).toBeDisabled();
    const pending = gate();
    state.failReads = false;
    state.readGate = pending.promise;
    await card(page)
      .getByRole('button', { name: '최신 구역 설정 다시 불러오기' })
      .click();
    await expect(card(page).getByRole('textbox')).toBeDisabled();
    await card(page, 'B')
      .getByRole('textbox')
      .fill('Editable during A refresh');
    pending.resolve();
    await card(page)
      .getByRole('button', { name: '내 입력 유지하고 다시 저장 준비' })
      .click();
    await expect(
      card(page).getByText(
        risk == null ? '위험도 (미설정)' : '위험도 5단계 · 매우 높음',
        { exact: true },
      ),
    ).toBeVisible();
    expect(state.writes).toHaveLength(1);
    await card(page).getByRole('button', { name: '저장', exact: true }).click();
    await expect(card(page).getByRole('textbox')).toBeEnabled();
    expect(state.writes[1].body).not.toHaveProperty('risk_level');
    expect(state.zones.get('A')?.risk_level).toBe(risk);
    await expect(card(page, 'B').getByRole('textbox')).toHaveValue(
      'Editable during A refresh',
    );
  });
}

test('save after deletion requires explicit create preparation and a separate save', async ({
  page,
}) => {
  const state = server();
  await mount(page, state);
  await card(page).getByRole('textbox').fill('Local draft from deleted zone');
  state.zones.delete('A');
  await card(page).getByRole('button', { name: '저장', exact: true }).click();
  await expect(card(page).getByRole('alert')).toContainText(
    '서버에서 이 구역이 삭제되었습니다',
  );
  expect(state.zones.has('A')).toBe(false);
  await card(page)
    .getByRole('button', { name: '내 입력으로 새 구역 만들기 준비' })
    .click();
  expect(state.zones.has('A')).toBe(false);
  await card(page).getByRole('button', { name: '저장', exact: true }).click();
  await expect(card(page).getByRole('textbox')).toBeEnabled();
  expect(state.writes[1].expected).toBeNull();
  expect(state.zones.get('A')?.rules).toEqual([
    'Local draft from deleted zone',
  ]);
});

test('stale delete preserves newer server settings and resolves before a fresh delete', async ({
  page,
}) => {
  const state = server();
  await mount(page, state);
  await card(page).getByRole('textbox').fill('My unsaved draft');
  state.zones.set('A', {
    ...zone('A'),
    revision: token(++state.sequence),
    rules: ['Newer than delete snapshot'],
  });
  await card(page).getByRole('button', { name: '삭제', exact: true }).click();
  await card(page)
    .getByRole('alertdialog')
    .getByRole('button', { name: '삭제', exact: true })
    .click();
  await expect(card(page).getByRole('alert')).toContainText(
    'Newer than delete snapshot',
  );
  await expect(card(page).getByRole('textbox')).toHaveValue('My unsaved draft');
  expect(state.zones.has('A')).toBe(true);
  await card(page)
    .getByRole('button', { name: '최신 내용으로 바꾸기' })
    .click();
  await card(page).getByRole('button', { name: '삭제', exact: true }).click();
  await card(page)
    .getByRole('alertdialog')
    .getByRole('button', { name: '삭제', exact: true })
    .click();
  await expect(card(page)).toHaveCount(0);
  expect(state.writes.map((w) => [w.method, w.status, w.expected])).toEqual([
    ['DELETE', 409, token(1)],
    ['DELETE', 200, token(2)],
  ]);
});
