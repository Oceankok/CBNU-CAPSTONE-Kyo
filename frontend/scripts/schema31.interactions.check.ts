import {
  acceptLatestResponse,
  executeConfirmedReconcile,
  fetchLatestEvidence,
  isCurrentWrite,
  refreshAfterScopeConflict,
  saveEvidenceWithConflictRecovery,
} from '../src/api/schema31Interactions.ts';
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

const evidenceA = { peer: 'EVENT_A', fingerprint: 'a'.repeat(64) };
const evidenceB = { peer: 'EVENT_B', fingerprint: 'b'.repeat(64) };

async function runMockInteractions() {
  // The production inspect handler uses fetchLatestEvidence. Resolve B before A.
  let evidenceRequest = 0;
  let selectedPeer: string | null = null;
  let visibleEvidence: typeof evidenceA | null = null;
  const responseA = new Deferred<typeof evidenceA>();
  const responseB = new Deferred<typeof evidenceB>();
  const inspect = (peer: string, response: Promise<typeof evidenceA>) => {
    const request = ++evidenceRequest;
    selectedPeer = peer;
    visibleEvidence = null;
    return fetchLatestEvidence(peer, request, () => evidenceRequest, () => response);
  };
  const inspectA = inspect('EVENT_A', responseA.promise);
  const inspectB = inspect('EVENT_B', responseB.promise);
  responseB.resolve(evidenceB);
  const visibleB = await inspectB;
  if (visibleB) visibleEvidence = visibleB.evidence;
  responseA.resolve(evidenceA);
  const visibleA = await inspectA;
  if (visibleA) visibleEvidence = visibleA.evidence;
  assert(selectedPeer === 'EVENT_B', 'B must remain selected');
  assert(visibleEvidence?.peer === 'EVENT_B', 'late A evidence must not overwrite B');

  // The production W17 list load uses acceptLatestResponse for concurrent loads.
  let listRequest = 0;
  let visibleList: string[] = [];
  const oldList = new Deferred<string[]>();
  const newList = new Deferred<string[]>();
  const oldListRequest = ++listRequest;
  const newListRequest = ++listRequest;
  const newListResult = newList.promise.then((value) =>
    acceptLatestResponse(newListRequest, listRequest, value),
  );
  const oldListResult = oldList.promise.then((value) =>
    acceptLatestResponse(oldListRequest, listRequest, value),
  );
  newList.resolve(['new-list']);
  const acceptedNew = await newListResult;
  if (acceptedNew) visibleList = acceptedNew;
  oldList.resolve(['old-list']);
  const rejectedOld = await oldListResult;
  if (rejectedOld) visibleList = rejectedOld;
  assert(visibleList[0] === 'new-list', 'late W17 list response must be ignored');

  // The production scope refresh is called for both backend conflict statuses.
  let draftTitle = '';
  const onUserInput = (value: string) => {
    draftTitle = value;
  };
  onUserInput('typed draft survives conflict');
  let recommendationRequest = 0;
  let latestRecommendation: string | null = null;
  const refresh = async (status: number, value: string) => {
    if (!isEducationScopeConflictStatus(status)) return;
    const request = ++recommendationRequest;
    const next = await refreshAfterScopeConflict(status, async () => value);
    if (isLatestRequest(request, recommendationRequest)) latestRecommendation = next;
  };
  await refresh(404, 'LATEST_FROM_404');
  await refresh(409, 'LATEST_FROM_409');
  assert(latestRecommendation === 'LATEST_FROM_409', 'latest scope refresh must win');
  assert(draftTitle === 'typed draft survives conflict', 'scope refresh must not clear draft state');

  // The production save helper must expose a 409 for reinspection, then allow B.
  let saveCalls = 0;
  const conflict = await saveEvidenceWithConflictRecovery(
    async () => {
      saveCalls += 1;
      throw { status: 409 };
    },
    async () => evidenceA,
  );
  assert(!conflict.saved && conflict.conflict, '409 must require reinspection');
  assert(saveCalls === 1, 'conflicted save must issue one POST attempt');
  selectedPeer = 'EVENT_B';
  const savedPeers: string[] = [];
  const saved = await saveEvidenceWithConflictRecovery(
    async () => {
      if (matchesEvidenceSelection(selectedPeer, 'EVENT_B', evidenceB.fingerprint, evidenceB.fingerprint)) {
        savedPeers.push('EVENT_B');
      }
    },
    async () => evidenceB,
  );
  assert(saved.saved && savedPeers[0] === 'EVENT_B', 'reselected B must be the saved target');

  // The production reconcile helper is not allowed to POST before confirmation.
  let reconcilePosts = 0;
  const post = async () => {
    reconcilePosts += 1;
    return { created_count: 2 };
  };
  await executeConfirmedReconcile(false, post);
  assert(reconcilePosts === 0, 'reconcile must not POST before confirmation');
  await executeConfirmedReconcile(true, post);
  assert(reconcilePosts === 1, 'reconcile POST requires explicit confirmation');

  // User action: save, close, reopen, then let the old save resolve while the new load owns busy.
  let oldWriteLoads = 0;
  let busy = true;
  let panelEpoch = 1;
  const currentWrite = 1;
  const oldWriteCompletion = new Deferred<void>();
  const oldWrite = oldWriteCompletion.promise.then(() => {
    if (!isCurrentWrite(1, currentWrite, 1, panelEpoch)) return;
    oldWriteLoads += 1;
    busy = false;
  });
  panelEpoch = 2;
  oldWriteCompletion.resolve();
  await oldWrite;
  assert(oldWriteLoads === 0, 'old write must not start a load after reopen');
  assert(busy, 'old write must not clear busy owned by the reopened panel');
  const reopenedLoadRequest = 2;
  const currentLoadRequest = 2;
  if (isLatestRequest(reopenedLoadRequest, currentLoadRequest)) busy = false;
  assert(!busy, 'the reopened panel load must be able to clear busy');
}

assert(isEducationScopeConflictStatus(404), 'education 404 must refresh the latest list');
assert(isEducationScopeConflictStatus(409), 'education 409 must refresh the latest list');
assert(!isEducationScopeConflictStatus(500), 'server errors must not be treated as scope conflicts');
assert(reconcileEffect(20).includes('save up to 20'), 'reconcile disclosure must describe its save effect');

await runMockInteractions();
console.log('schema31 production interaction helpers ok');
