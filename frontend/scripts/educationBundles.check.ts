import {
  bundleCreateRequest,
  bundleGenerateRequest,
  bundlePublishRequest,
  bundleSelectionRequest,
  bundleVersionRequest,
} from '../src/api/educationBundleContracts.ts';

const assert = (condition: unknown, message: string): asserts condition => {
  if (!condition) throw new Error(message);
};

const ids = ['REC_A', 'REC_B'];
const content = { title: '공통 교육', body: '검토 본문', checklist: ['착용 확인'] };

assert(
  JSON.stringify(bundleSelectionRequest(ids)) === '{"recommendation_ids":["REC_A","REC_B"]}',
  'preview must send recommendation_ids only',
);
assert(
  JSON.stringify(bundleCreateRequest(ids, 'a'.repeat(64), content)) ===
    JSON.stringify({ recommendation_ids: ids, expected_snapshot: 'a'.repeat(64), ...content }),
  'manual create must carry the preview snapshot and content',
);
assert(
  JSON.stringify(bundleGenerateRequest(ids, 'b'.repeat(64))) ===
    JSON.stringify({ recommendation_ids: ids, expected_snapshot: 'b'.repeat(64) }),
  'generation must carry the preview snapshot without draft content',
);
assert(
  JSON.stringify(bundlePublishRequest(3)) === '{"expected_version":3,"approved":true}',
  'publish must be explicit and versioned',
);
assert(
  JSON.stringify(bundleVersionRequest(4)) === '{"expected_version":4}',
  'unpublish and update must carry expected_version',
);
ids.push('REC_C');
assert(bundleSelectionRequest(['REC_A', 'REC_B']).recommendation_ids.length === 2, 'request helper must not mutate input');
console.log('education bundle contracts ok');
