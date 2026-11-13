import {
  bundleCreateRequest,
  bundleGenerateRequest,
  bundlePublishRequest,
  bundleSelectionRequest,
  bundleVersionRequest,
} from '../src/api/educationBundleContracts.ts';
import {
  bundleActionBlocked,
  bundleDraftIsDirty,
  previewIsCurrent,
} from '../src/api/educationBundleEditorContracts.ts';
import type { EducationBundle } from '../src/types/index.ts';

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

const savedBundle = {
  title: '공통 교육',
  body: '서버 본문',
  checklist: ['착용 확인'],
  status: 'draft',
  version: 4,
} as EducationBundle;
const savedDraft = { title: '공통 교육', body: '서버 본문', checklist: '착용 확인' };
assert(!bundleDraftIsDirty(savedBundle, savedDraft), 'saved draft must be clean');
assert(
  bundleDraftIsDirty(savedBundle, { ...savedDraft, body: '내가 수정한 본문' }),
  'typed changes must be dirty',
);
assert(
  bundleActionBlocked(savedBundle, { ...savedDraft, body: '내가 수정한 본문' }, false),
  'publish must be blocked while unsaved changes exist',
);
assert(
  bundleActionBlocked(savedBundle, savedDraft, true),
  'save and publish must be blocked until a 409 is explicitly resolved',
);
assert(!previewIsCurrent('old-selection', 'new-selection'), 'stale snapshot must not match a new selection');
assert(previewIsCurrent('same-selection', 'same-selection'), 'same selection may use its preview snapshot');
console.log('education bundle editor safety contracts ok');
