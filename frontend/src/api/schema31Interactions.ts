import {
  isEducationScopeConflictStatus,
  isLatestRequest,
} from './schema31Contracts.ts';

export function acceptLatestResponse<T>(
  requestId: number,
  currentRequestId: number,
  value: T,
): T | null {
  return isLatestRequest(requestId, currentRequestId) ? value : null;
}

export async function fetchLatestEvidence<T>(
  peerId: string,
  requestId: number,
  currentRequestId: () => number,
  fetchEvidence: (peerId: string) => Promise<T>,
): Promise<{ peerId: string; evidence: T } | null> {
  const evidence = await fetchEvidence(peerId);
  return isLatestRequest(requestId, currentRequestId())
    ? { peerId, evidence }
    : null;
}

export async function refreshAfterScopeConflict<T>(
  status: number,
  fetchLatest: () => Promise<T>,
): Promise<T | null> {
  if (!isEducationScopeConflictStatus(status)) return null;
  return fetchLatest();
}

export async function saveEvidenceWithConflictRecovery<T>(
  save: () => Promise<unknown>,
  refreshEvidence: () => Promise<T>,
): Promise<{ saved: true } | { saved: false; conflict: true; evidence: T }> {
  try {
    await save();
    return { saved: true };
  } catch (error) {
    if (!(error && typeof error === 'object' && 'status' in error && error.status === 409)) {
      throw error;
    }
    return { saved: false, conflict: true, evidence: await refreshEvidence() };
  }
}

export function executeConfirmedReconcile<T>(
  confirmed: boolean,
  post: () => Promise<T>,
): Promise<T | null> {
  return confirmed ? post() : Promise.resolve(null);
}
