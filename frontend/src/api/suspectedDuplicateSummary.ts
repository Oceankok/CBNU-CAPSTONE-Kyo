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
}

const relationPeerId = (eventId: string, relation: SavedSuspectedRelation) =>
  relation.event_a === eventId ? relation.event_b : relation.event_a;

const uniqueWithoutSelf = (eventId: string, ids: string[]) =>
  [...new Set(ids)].filter((peerId) => peerId && peerId !== eventId);

export function buildSuspectedDuplicateSummary(
  eventId: string,
  preview: SuspectedDuplicatePreview | null,
  saved: SavedSuspectedRelation[],
): SuspectedDuplicateSummary {
  const computedPeerIds = uniqueWithoutSelf(
    eventId,
    preview?.items.map((candidate) => candidate.event_id) ?? [],
  );
  const activeSavedPeerIds = uniqueWithoutSelf(
    eventId,
    saved
      .filter((relation) => relation.state === 'saved' && !relation.stale)
      .map((relation) => relationPeerId(eventId, relation)),
  );
  const historicalPeerIds = uniqueWithoutSelf(
    eventId,
    saved
      .filter((relation) => relation.state === 'released' || relation.stale)
      .map((relation) => relationPeerId(eventId, relation)),
  );

  return {
    computedPeerIds,
    activeSavedPeerIds,
    computedOnlyPeerIds: computedPeerIds.filter(
      (peerId) => !activeSavedPeerIds.includes(peerId),
    ),
    historicalPeerIds,
    staleCount: saved.filter((relation) => relation.stale).length,
    releasedCount: saved.filter((relation) => relation.state === 'released').length,
  };
}
