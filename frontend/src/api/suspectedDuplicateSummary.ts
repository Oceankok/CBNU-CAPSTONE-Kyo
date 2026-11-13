import type {
  SavedSuspectedRelation,
  SuspectedDuplicatePreview,
} from './events';

export interface SuspectedDuplicateSummary {
  computedPeerIds: string[];
  activeSavedPeerIds: string[];
  computedOnlyPeerIds: string[];
  historicalPeerIds: string[];
  staleCount: number;
  releasedCount: number;
  partialResults: boolean;
  savedUnavailable: boolean;
}

const relationPeerId = (eventId: string, relation: SavedSuspectedRelation) =>
  relation.event_a === eventId ? relation.event_b : relation.event_a;

const uniqueWithoutSelf = (eventId: string, ids: string[]) =>
  [...new Set(ids)].filter((peerId) => peerId && peerId !== eventId);

export function buildSuspectedDuplicateSummary(
  eventId: string,
  preview: SuspectedDuplicatePreview | null,
  saved: SavedSuspectedRelation[] | null,
  savedHasMore = false,
): SuspectedDuplicateSummary {
  const savedUnavailable = saved === null;
  const partialResults = Boolean(preview?.has_more || savedHasMore || savedUnavailable);
  const savedItems = saved ?? [];
  const computedPeerIds = uniqueWithoutSelf(
    eventId,
    preview?.items.map((candidate) => candidate.event_id) ?? [],
  );
  const activeSavedPeerIds = uniqueWithoutSelf(
    eventId,
    savedItems
      .filter((relation) => relation.state === 'saved' && !relation.stale)
      .map((relation) => relationPeerId(eventId, relation)),
  );
  const historicalPeerIds = uniqueWithoutSelf(
    eventId,
    savedItems
      .filter((relation) => relation.state === 'released' || relation.stale)
      .map((relation) => relationPeerId(eventId, relation)),
  );

  return {
    computedPeerIds,
    activeSavedPeerIds,
    computedOnlyPeerIds: partialResults
      ? []
      : computedPeerIds.filter((peerId) => !activeSavedPeerIds.includes(peerId)),
    historicalPeerIds,
    staleCount: savedItems.filter((relation) => relation.stale).length,
    releasedCount: savedItems.filter((relation) => relation.state === 'released').length,
    partialResults,
    savedUnavailable,
  };
}
