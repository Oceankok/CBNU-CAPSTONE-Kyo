import {
  appendRepeatedZones,
  materialCreateBody,
  materialGenerateBody,
  relationFingerprintBody,
  zoneSaveBody,
} from '../src/api/schema31Contracts.ts';
import { buildSuspectedDuplicateSummary } from '../src/api/suspectedDuplicateSummary.ts';
import type { SavedSuspectedRelation } from '../src/api/events.ts';

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

const relation = (
  event_a: string,
  event_b: string,
  state: 'saved' | 'released',
  stale = false,
): SavedSuspectedRelation => ({
  relation_id: `${event_a}-${event_b}`,
  event_a,
  event_b,
  fingerprint: 'f'.repeat(64),
  evidence: {},
  saved_by: 'admin01',
  saved_at: '2026-01-01T00:00:00Z',
  released_by: state === 'released' ? 'admin01' : null,
  released_at: state === 'released' ? '2026-01-02T00:00:00Z' : null,
  state,
  stale,
  current_fingerprint: stale ? null : 'f'.repeat(64),
  current_reason: stale ? 'changed' : null,
  suppresses_preview: false,
  automatic_merge: false,
  statistics_changed: false,
});

const duplicateSummary = buildSuspectedDuplicateSummary(
  'EVENT_SELF',
  {
    event_id: 'EVENT_SELF',
    mode: 'metadata_preview',
    eligible: true,
    reason: null,
    total: 2,
    limit: 20,
    offset: 0,
    has_more: false,
    next_offset: null,
    pagination_truncated: false,
    items: [
      {
        event_id: 'EVENT_SELF',
        camera_id: 'CAM',
        ppe_type: 'helmet',
        timestamp_start: '',
        timestamp_end: '',
        event_status: 'pending',
        overlap_seconds: 1,
        shared_group_snapshots: [],
        reason: '',
      },
      {
        event_id: 'EVENT_B',
        camera_id: 'CAM',
        ppe_type: 'helmet',
        timestamp_start: '',
        timestamp_end: '',
        event_status: 'pending',
        overlap_seconds: 1,
        shared_group_snapshots: [],
        reason: '',
      },
    ],
  },
  [
    relation('EVENT_SELF', 'EVENT_B', 'saved'),
    relation('EVENT_SELF', 'EVENT_C', 'saved', true),
    relation('EVENT_SELF', 'EVENT_D', 'released'),
  ],
);
assert(
  duplicateSummary.computedPeerIds.join(',') === 'EVENT_B',
  'summary must exclude self and show current computed peers',
);
assert(
  duplicateSummary.activeSavedPeerIds.join(',') === 'EVENT_B',
  'summary must show current saved peers',
);
assert(
  duplicateSummary.computedOnlyPeerIds.length === 0,
  'summary must distinguish saved current candidates',
);
assert(
  duplicateSummary.historicalPeerIds.join(',') === 'EVENT_C,EVENT_D',
  'summary must retain stale and released history peers',
);
assert(
  duplicateSummary.staleCount === 1 && duplicateSummary.releasedCount === 1,
  'summary must count stale and released relations',
);
const partialSummary = buildSuspectedDuplicateSummary(
  'EVENT_SELF',
  {
    event_id: 'EVENT_SELF',
    mode: 'metadata_preview',
    eligible: true,
    reason: null,
    total: 21,
    limit: 20,
    offset: 0,
    has_more: true,
    next_offset: 20,
    pagination_truncated: true,
    items: [],
  },
  [relation('EVENT_SELF', 'EVENT_B', 'saved')],
  true,
);
assert(partialSummary.partialResults, 'summary must expose truncated relation results');
assert(
  partialSummary.computedOnlyPeerIds.length === 0,
  'summary must not claim an unsaved difference when either list is truncated',
);
console.log('schema31 contracts ok');
