import type { EducationBundle } from '../types';

export interface BundleDraftFields {
  title: string;
  body: string;
  checklist: string;
}

export function bundleDraftIsDirty(
  bundle: EducationBundle,
  draft: BundleDraftFields,
): boolean {
  const checklist = draft.checklist
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
  return (
    draft.title !== bundle.title ||
    draft.body !== bundle.body ||
    JSON.stringify(checklist) !== JSON.stringify(bundle.checklist)
  );
}

export function bundleActionBlocked(
  bundle: EducationBundle,
  draft: BundleDraftFields,
  conflictNeedsReview: boolean,
): boolean {
  return bundleDraftIsDirty(bundle, draft) || conflictNeedsReview;
}

export function previewIsCurrent(
  previewSelectionKey: string | null,
  selectionKey: string,
): boolean {
  return Boolean(previewSelectionKey && previewSelectionKey === selectionKey);
}

export function bundleMutationIsAllowed(
  bundleId: string,
  bundleVersion: number,
  activeBundleId: string | null,
  activeBundleVersion: number | null,
  actionBlocked: boolean,
): boolean {
  if (activeBundleId !== null && activeBundleId === bundleId && activeBundleVersion !== bundleVersion) {
    return false;
  }
  return !actionBlocked;
}

export function bundleConflictResponseIsCurrent(
  requestEpoch: number,
  currentEpoch: number,
  requestedBundleId: string,
  activeBundleId: string | null,
): boolean {
  return requestEpoch === currentEpoch && requestedBundleId === activeBundleId;
}
