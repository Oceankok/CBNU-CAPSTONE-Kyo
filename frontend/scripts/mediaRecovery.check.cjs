// Actual EventMediaPanel TSX with isolated hook/API doubles; not browser or video validation.
// Run from frontend: node scripts/mediaRecovery.check.cjs
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const ts = require('typescript');
const tick = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };

function harness(initial) {
  let response = initial, cursor = 0, effects = [], tree, timerId = 0;
  const slots = [], timers = new Map();
  const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
  const react = {
    useState(initialValue) {
      const n = cursor++;
      if (!(n in slots)) slots[n] = initialValue;
      return [slots[n], next => { slots[n] = typeof next === 'function' ? next(slots[n]) : next; }];
    },
    useCallback(fn, deps) {
      const n = cursor++;
      if (!slots[n] || !same(slots[n].deps, deps)) slots[n] = { fn, deps };
      return slots[n].fn;
    },
    useEffect(fn, deps) {
      const n = cursor++;
      if (!slots[n] || !same(slots[n].deps, deps)) {
        slots[n]?.cleanup?.();
        slots[n] = { deps };
        effects.push(() => { slots[n].cleanup = fn(); });
      }
    },
  };
  const jsx = (type, props) => ({ type, props: props || {} });
  const api = { fetchEventMedia: async () => response };
  const source = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/components/EventMediaPanel.tsx'), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText.replaceAll('import.meta.env.DEV', 'false');
  const requireMock = name => {
    if (name === 'react') return react;
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'fragment' };
    if (name === '../api/events') return api;
    if (name === '../api/client') return { mediaUrl: value => value };
    if (name === '../api/consents') return { CONSENT_ERRORS: {} };
    if (name === '../time') return { formatSeoul: value => value };
    if (name.endsWith('.css')) return { default: {} };
    if (name === './ConfirmButton' || name === './ConsentUploadForm') return { default: () => null };
    throw Error('Unexpected import: ' + name);
  };
  const mod = { exports: {} };
  vm.runInNewContext('(function(require,module,exports){' + source + '\n})', {
    console,
    window: {
      setInterval: fn => { timers.set(++timerId, fn); return timerId; },
      clearInterval: id => timers.delete(id),
      addEventListener() {}, removeEventListener() {},
    },
  })(requireMock, mod, mod.exports);
  const props = { eventId: 'g3-test', cameraId: 'CAM_A', finalReview: false, onChange: async () => {} };
  const render = () => {
    cursor = 0; effects = [];
    tree = mod.exports.default(props);
    effects.forEach(fn => fn());
  };
  const text = node => Array.isArray(node) ? node.map(text).join('') : node == null || typeof node === 'boolean'
    ? '' : typeof node === 'object' ? text(node.props.children) : String(node);
  const all = node => Array.isArray(node) ? node.flatMap(child => all(child)) :
    node && typeof node === 'object' ? [node, ...all(node.props.children)] : [];
  return { render, timers, all: () => all(tree), text: () => text(tree), setResponse: value => { response = value; } };
}

const primary = { media_id: 'primary', camera_id: 'CAM_A', kind: 'video', role: 'clip',
  status: 'ready', url: '/storage/redacted-primary.mp4', redaction_status: 'complete' };
const request = { request_id: 'reference', camera_id: 'CAM_B', status: 'pending' };
const body = (req, media = []) => ({ items: [primary, ...media], clip_requests: [req], retention: null,
  review_availability: { state: 'available', can_review: true } });

async function run() {
  let checks = 0;
  for (const [req, media, expected] of [
    [request, [], '영상 수신 대기'],
    [{ ...request, status: 'unavailable', error_code: 'clip_delivery_timeout' }, [], '영상 확보 불가 · 수신 시간 초과'],
    [{ ...request, status: 'unavailable' }, [], '영상 확보 불가'],
    [{ ...request, status: 'failed' }, [], '영상 수집 실패 · 복구 확인 필요'],
    [{ ...request, status: 'received' }, [{ media_id: 'r', clip_request_id: 'reference', status: 'processing' }], '얼굴 가림 처리 중'],
    [{ ...request, status: 'received' }, [{ media_id: 'r', clip_request_id: 'reference', status: 'failed' }], '영상 처리 실패'],
    [{ ...request, status: 'received' }, [], '수신 기록은 있으나 확인 가능한 영상 없음'],
    [{ ...request, status: 'received' }, [{ ...primary, media_id: 'r', camera_id: 'CAM_B', clip_request_id: 'reference' }], '영상 확인 가능'],
  ]) {
    const h = harness(body(req, media)); h.render(); await tick(); h.render();
    assert.ok(h.text().includes(expected), expected);
    assert.ok(h.text().includes('CAM_B'));
    assert.ok(h.all().some(n => n.type === 'video' && n.props.src === primary.url));
    assert.ok(h.all().some(n => n.type === 'button' && n.props.children === '▶ 클립 재생'));
    checks++;
  }
  const legacy = harness({ items: [primary], retention: null });
  legacy.render(); await tick(); legacy.render();
  assert.ok(!legacy.text().includes('카메라별 영상 확보 상태'));
  checks++;

  const polling = harness(body(request)); polling.render(); await tick(); polling.render();
  assert.equal(polling.timers.size, 1);
  polling.setResponse(body({ ...request, status: 'unavailable', error_code: 'clip_delivery_timeout' }));
  for (const fn of [...polling.timers.values()]) await fn();
  await tick(); polling.render();
  assert.ok(polling.text().includes('수신 시간 초과'));
  assert.equal(polling.timers.size, 0, 'stop polling terminal requests');
  const tab = polling.all().find(n => n.props.role === 'tab' && n.props.children === 'CAM_B');
  assert.ok(tab, 'camera with missing reference is selectable');
  tab.props.onClick(); polling.render();
  assert.ok(polling.text().includes('연결된 이미지·영상이 없습니다'));
  checks++;
  console.log(JSON.stringify({ checks, result: 'passed', scope: 'TSX hook/API doubles; no browser/video proof' }));
}
run().catch(error => { console.error(error); process.exitCode = 1; });
