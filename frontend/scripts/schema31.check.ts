import {
  appendRepeatedZones,
  materialCreateBody,
  materialGenerateBody,
  relationFingerprintBody,
  zoneSaveBody,
} from '../src/api/schema31Contracts.ts';

const assert = (condition: unknown, message: string): asserts condition => {
  if (!condition) throw new Error(message);
};

const scope = {
  recommendation_id: 'REC_1',
  quarter: '2026-Q4',
  zone_name: 'A',
  ppe_type: 'helmet',
  education_topic: 'topic',
};

const params = appendRepeatedZones(new URLSearchParams(), ['A', 'B']);
assert(params.toString() === 'zones=A&zones=B', 'summary must repeat zones');
assert(materialGenerateBody(scope).expected_scope === scope, 'generation must send expected_scope');
assert(materialCreateBody({ title: 't', body: 'b', checklist: ['c'] }, scope).expected_scope === scope, 'direct creation must send full expected_scope');
assert(relationFingerprintBody('a'.repeat(64)).expected_fingerprint.length === 64, 'relation actions must guard with fingerprint');
assert(!('risk_level' in zoneSaveBody({ required_ppe: [], rules: [] })), 'rules-only save must omit risk_level');
assert(zoneSaveBody({ required_ppe: [], rules: [], risk_level: 5 }).risk_level === 5, 'explicit risk change must send risk_level');
console.log('schema31 contracts ok');
