import assert from 'node:assert/strict';
import { maySelectEnforce, settingsSaveBlocker, settingsUpdate, settingsPreview } from '../src/autoDecisionSettings.ts';
import { formatErrorDetail } from '../src/api/errorDetail.ts';
import type { AutoDecisionSettings } from '../src/api/autoDecisions.ts';
const base: AutoDecisionSettings = {version: 7, mode: 'off', threshold: 0.9,
  model_versions: [], ppe_types: [], camera_ids: [], zone_names: [], validation_reference: null,
  activation_blockers: ['validated_adapter_unavailable'], automatic_confirmation_enabled: false};
const before = JSON.stringify(base);
const request = settingsUpdate(base, false, '  관리자 수정  ');
assert.deepEqual(request, {expected_version: 7, mode: 'off', threshold: 0.9,
  model_versions: [], ppe_types: [], camera_ids: [], zone_names: [], validation_reference: null, reason: '관리자 수정'});
assert.equal('activation_blockers' in request, false);
assert.equal('automatic_confirmation_enabled' in request, false);
assert.throws(() => settingsUpdate(base, true, '사유')); // 409 cannot silently advance version.
assert.equal(JSON.stringify(base), before); // Local input and expected version preserved.
assert.equal(maySelectEnforce(null), false);
assert.equal(maySelectEnforce({...base, activation_blockers: undefined} as unknown as AutoDecisionSettings), false);
assert.equal(maySelectEnforce(base), false);
assert.throws(() => settingsUpdate({...base, mode: 'enforce'}, false, '사유'));
assert.throws(() => settingsUpdate({...base, mode: 'shadow'}, false, '사유'));
const scoped = {...base, mode: 'shadow' as const, model_versions: ['validated-model'], ppe_types: ['helmet'], camera_ids: ['CAM_TEST']};
assert.equal(settingsSaveBlocker(scoped, false, '사유'), null);
assert.equal(settingsUpdate(scoped, false, '사유').zone_names.length, 0); // No extra zone restriction.
for (const threshold of [-1, 1.1, NaN, Infinity]) assert.throws(() => settingsUpdate({...base, threshold}, false, '사유'));
assert.throws(() => settingsUpdate(base, false, '  '));
assert.equal(formatErrorDetail({code: 'settings_changed', version: 8}), '{"code":"settings_changed","version":8}');
assert.equal(formatErrorDetail([{msg: 'invalid threshold'}]), 'invalid threshold');
assert.equal(formatErrorDetail('forbidden'), 'forbidden');
assert.equal(formatErrorDetail(null), '요청 처리 실패');
console.log('autoDecisionSettings contract checks ok (synthetic, no browser or server)');

const diagnosis = settingsPreview({...scoped, zone_names: ['ZONE_TEST']}, ['EVENT_TEST']);
assert.deepEqual(diagnosis.camera_ids, ['CAM_TEST']);
assert.deepEqual(diagnosis.zone_names, ['ZONE_TEST']);
assert.deepEqual(diagnosis.event_ids, ['EVENT_TEST']);
assert.equal('activation_blockers' in diagnosis, false);
