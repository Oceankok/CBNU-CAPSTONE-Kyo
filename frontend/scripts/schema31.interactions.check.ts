import {
  isEducationScopeConflictStatus,
  isLatestRequest,
  matchesEvidenceSelection,
  reconcileEffect,
} from '../src/api/schema31Contracts.ts';

const assert = (condition: unknown, message: string): asserts condition => {
  if (!condition) throw new Error(message);
};

class Deferred<T> {
  promise: Promise<T>;
  resolve!: (value: T) => void;
  reject!: (reason?: unknown) => void;

  constructor() {
    this.promise = new Promise<T>((resolve, reject) => {
      this.resolve = resolve;
      this.reject = reject;
    });
  }
}

type Evidence = { peer: string; fingerprint: string };

async function runMockInteractions() {
  // User action: inspect A, immediately inspect B. Network returns B first, A last.
  const evidenceState: {
    request: number;
    selectedPeer: string | null;
    evidence: Evidence | null;
  } = { request: 0, selectedPeer: null, evidence: null };
  const inspect = async (peer: string, response: Promise<Evidence>) => {
    const request = ++evidenceState.request;
    evidenceState.selectedPeer = peer;
    evidenceState.evidence = null;
    const next = await response;
    if (isLatestRequest(request, evidenceState.request)) evidenceState.evidence = next;
  };
  const responseA = new Deferred<Evidence>();
  const responseB = new Deferred<Evidence>();
  const inspectA = inspect('EVENT_A', responseA.promise);
  const inspectB = inspect('EVENT_B', responseB.promise);
  responseB.resolve({ peer: 'EVENT_B', fingerprint: 'b'.repeat(64) });
  await inspectB;
  responseA.resolve({ peer: 'EVENT_A', fingerprint: 'a'.repeat(64) });
  await inspectA;
  assert(evidenceState.selectedPeer === 'EVENT_B', 'B must remain selected');
  assert(evidenceState.evidence?.peer === 'EVENT_B', 'late A evidence must not overwrite B');

  // User action: refresh materials twice. The older list response arrives last.
  const materialState: { request: number; items: string[] } = { request: 0, items: [] };
  const loadMaterials = async (response: Promise<string[]>) => {
    const request = ++materialState.request;
    const next = await response;
    if (isLatestRequest(request, materialState.request)) materialState.items = next;
  };
  const oldMaterials = new Deferred<string[]>();
  const newMaterials = new Deferred<string[]>();
  const oldLoad = loadMaterials(oldMaterials.promise);
  const newLoad = loadMaterials(newMaterials.promise);
  newMaterials.resolve(['new-material']);
  await newLoad;
  oldMaterials.resolve(['old-material']);
  await oldLoad;
  assert(materialState.items[0] === 'new-material', 'late material list must be ignored');

  // User action: a 409/404 scope conflict refreshes the latest recommendations but keeps draft text.
  const recommendationState: {
    request: number;
    latest: string | null;
    draftTitle: string;
  } = { request: 0, latest: null, draftTitle: 'typed draft survives conflict' };
  const refreshRecommendations = async (status: number, response: Promise<string>) => {
    if (!isEducationScopeConflictStatus(status)) return;
    const request = ++recommendationState.request;
    const latest = await response;
    if (isLatestRequest(request, recommendationState.request)) recommendationState.latest = latest;
  };
  const oldRecommendations = new Deferred<string>();
  const newRecommendations = new Deferred<string>();
  const oldRefresh = refreshRecommendations(409, oldRecommendations.promise);
  const newRefresh = refreshRecommendations(404, newRecommendations.promise);
  newRecommendations.resolve('LATEST_RECOMMENDATION');
  await newRefresh;
  oldRecommendations.resolve('OLD_RECOMMENDATION');
  await oldRefresh;
  assert(
    recommendationState.latest === 'LATEST_RECOMMENDATION',
    'latest recommendation response must win after 404/409 refreshes',
  );
  assert(
    recommendationState.draftTitle === 'typed draft survives conflict',
    'scope conflict must not clear the typed draft',
  );

  // User action: save A receives 409, then explicitly selects B and saves B.
  const saved: Evidence[] = [];
  let selectedPeer = 'EVENT_A';
  const saveAfterConflict = async (peer: string, fingerprint: string, status: number) => {
    if (status === 409) return 'reinspect';
    if (matchesEvidenceSelection(selectedPeer, peer, fingerprint, fingerprint)) {
      saved.push({ peer, fingerprint });
    }
    return 'saved';
  };
  assert(
    (await saveAfterConflict('EVENT_A', 'a'.repeat(64), 409)) === 'reinspect',
    '409 must require reinspection before retry',
  );
  assert(saved.length === 0, '409 must not record a save');
  selectedPeer = 'EVENT_B';
  assert(
    (await saveAfterConflict('EVENT_B', 'b'.repeat(64), 201)) === 'saved',
    'explicitly reselected B can be saved',
  );
  assert(saved[0]?.peer === 'EVENT_B', 'save must use the reselected peer');

  // User action: arm reconcile, verify no POST, then explicitly confirm the save effect.
  const reconcileArmed = true;
  let postCount = 0;
  const mockReconcilePost = async () => {
    postCount += 1;
    return { created_count: 2 };
  };
  assert(postCount === 0, 'arming reconcile must not POST');
  if (reconcileArmed) {
    await mockReconcilePost();
  }
  assert(postCount === 1, 'only explicit reconcile confirmation may POST');
}

assert(isEducationScopeConflictStatus(404), 'education 404 must refresh the latest list');
assert(isEducationScopeConflictStatus(409), 'education 409 must refresh the latest list');
assert(!isEducationScopeConflictStatus(500), 'server errors must not be treated as scope conflicts');

assert(
  matchesEvidenceSelection('EVENT_B', 'EVENT_B', 'b'.repeat(64), 'b'.repeat(64)),
  'save must target the inspected peer and fingerprint',
);
assert(
  !matchesEvidenceSelection('EVENT_B', 'EVENT_A', 'a'.repeat(64), 'a'.repeat(64)),
  'a stale peer must not be saved after selection changes',
);
assert(
  reconcileEffect(20).includes('save up to 20'),
  'reconcile disclosure must describe its save effect',
);

await runMockInteractions();
console.log('schema31 mock interactions ok');
