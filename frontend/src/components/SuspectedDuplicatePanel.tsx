import { useRef, useState } from 'react';
import {
  fetchRelationEvidence,
  fetchSavedSuspectedRelations,
  fetchSuspectedDuplicatesPreview,
  reconcileSuspectedRelations,
  releaseSuspectedRelation,
  saveSuspectedRelation,
} from '../api/events';
import type {
  RelationEvidence,
  SavedSuspectedRelation,
  SuspectedDuplicatePreview,
  SuspectedDuplicateTag,
} from '../api/events';
import { ApiError } from '../api/client';
import { isLatestRequest, matchesEvidenceSelection, reconcileEffect } from '../api/schema31Contracts';
import {
  acceptLatestResponse,
  executeConfirmedReconcile,
  fetchLatestEvidence,
  saveEvidenceWithConflictRecovery,
} from '../api/schema31Interactions';
import styles from './SuspectedDuplicatePanel.module.css';

const relationError = (error: unknown) =>
  error instanceof Error ? error.message : String(error);

const textValue = (value: unknown): string => {
  if (value == null) return '-';
  if (Array.isArray(value)) return value.map(textValue).join(', ');
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
};

function EvidenceDetails({ evidence }: { evidence: Record<string, unknown> }) {
  const events = Array.isArray(evidence.events) ? evidence.events : [];
  const groups = Array.isArray(evidence.shared_group_snapshots)
    ? evidence.shared_group_snapshots
    : [];
  return (
    <dl className={styles.evidenceDetails}>
      <div>
        <dt>Rule</dt>
        <dd>{textValue(evidence.rule_version)}</dd>
      </div>
      <div>
        <dt>Time basis</dt>
        <dd>
          {textValue(evidence.interval_basis)} · tolerance {textValue(evidence.clock_tolerance_seconds)}s
        </dd>
      </div>
      <div>
        <dt>Shared group snapshots</dt>
        <dd>
          {groups.length
            ? groups
                .map((group) => {
                  const item = group as Record<string, unknown>;
                  return `${textValue(item.group_id)} (v${textValue(item.group_version)})`;
                })
                .join(', ')
            : 'none'}
        </dd>
      </div>
      <div>
        <dt>Observed intervals</dt>
        <dd>
          {events.length
            ? events
                .map((event) => {
                  const item = event as Record<string, unknown>;
                  return `${textValue(item.event_id)}: ${textValue(item.timestamp_start)} ~ ${textValue(item.timestamp_end)}`;
                })
                .join(' · ')
            : 'none'}
        </dd>
      </div>
    </dl>
  );
}

export default function SuspectedDuplicatePanel({
  eventId,
  tag,
}: {
  eventId: string;
  tag?: SuspectedDuplicateTag;
}) {
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState<SuspectedDuplicatePreview | null>(null);
  const [saved, setSaved] = useState<SavedSuspectedRelation[]>([]);
  const [savedTotal, setSavedTotal] = useState(0);
  const [savedHasMore, setSavedHasMore] = useState(false);
  const [selectedPeer, setSelectedPeer] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<RelationEvidence | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reconcileArmed, setReconcileArmed] = useState(false);
  const evidenceRequest = useRef(0);
  const loadRequest = useRef(0);

  const load = async (): Promise<boolean> => {
    const request = ++loadRequest.current;
    try {
      const [nextPreview, nextSaved] = await Promise.all([
        fetchSuspectedDuplicatesPreview(eventId),
        fetchSavedSuspectedRelations(eventId),
      ]);
      const result = acceptLatestResponse(request, loadRequest.current, {
        preview: nextPreview,
        saved: nextSaved,
      });
      if (!result) return false;
      setPreview(result.preview);
      setSaved(result.saved.items);
      setSavedTotal(result.saved.total);
      setSavedHasMore(result.saved.has_more);
      return true;
    } catch (loadError) {
      if (!isLatestRequest(request, loadRequest.current)) return false;
      throw loadError;
    }
  };

  const openPanel = () => {
    setOpen(true);
    setBusy(true);
    setError(null);
    const request = loadRequest.current + 1;
    void load()
      .catch((loadError) => {
        if (isLatestRequest(request, loadRequest.current)) setError(relationError(loadError));
      })
      .finally(() => {
        if (isLatestRequest(request, loadRequest.current)) setBusy(false);
      });
  };

  const inspect = async (peerId: string) => {
    const request = ++evidenceRequest.current;
    setSelectedPeer(peerId);
    setEvidence(null);
    setError(null);
    try {
      const result = await fetchLatestEvidence(
        peerId,
        request,
        () => evidenceRequest.current,
        (peer) => fetchRelationEvidence(eventId, peer),
      );
      if (result) setEvidence(result.evidence);
    } catch (inspectError) {
      if (isLatestRequest(request, evidenceRequest.current)) setError(relationError(inspectError));
    }
  };

  const save = async () => {
    if (!selectedPeer || !evidence?.fingerprint || !evidence.eligible) return;
    const peerAtSave = selectedPeer;
    const fingerprintAtSave = evidence.fingerprint;
    const requestAtSave = evidenceRequest.current;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const saveResult = await saveEvidenceWithConflictRecovery(
        () => saveSuspectedRelation(eventId, peerAtSave, fingerprintAtSave),
        () => fetchRelationEvidence(eventId, peerAtSave),
      );
      if (!saveResult.saved) {
        if (isLatestRequest(requestAtSave, evidenceRequest.current) && selectedPeer === peerAtSave) {
          setEvidence(saveResult.evidence);
          setError('Evidence changed. Review the refreshed evidence before saving again.');
        }
        return;
      }
      if (!(await load())) return;
      if (
        !isLatestRequest(requestAtSave, evidenceRequest.current) ||
        !matchesEvidenceSelection(selectedPeer, peerAtSave, evidence?.fingerprint, fingerprintAtSave)
      ) {
        return;
      }
      setNotice('The selected evidence was saved. No merge, identity confirmation, or statistic change was performed.');
    } catch (saveError) {
      setError(relationError(saveError));
    } finally {
      setBusy(false);
    }
  };

  const release = async (relation: SavedSuspectedRelation) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await releaseSuspectedRelation(relation.relation_id, relation.fingerprint, eventId);
      if (!(await load())) return;
      setNotice('The saved relation was released. Computed candidates remain independent from release history.');
    } catch (releaseError) {
      setError(
        releaseError instanceof ApiError && releaseError.status === 409
          ? 'The saved evidence changed. Reload the current relation before releasing it.'
          : relationError(releaseError),
      );
      if (releaseError instanceof ApiError && releaseError.status === 409) {
        void load().catch(() => undefined);
      }
    } finally {
      setBusy(false);
    }
  };

  const reconcile = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = (await executeConfirmedReconcile(true, () =>
        reconcileSuspectedRelations(eventId),
      )) as { created_count?: number };
      if (!(await load())) return;
      setReconcileArmed(false);
      setNotice(`The explicit scan saved ${result.created_count ?? 0} new relation(s).`);
    } catch (reconcileError) {
      setError(relationError(reconcileError));
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button type="button" className={styles.openBtn} onClick={openPanel}>
        Review suspected relations{tag?.candidate_count ? ` (${tag.candidate_count})` : ''}
      </button>
    );
  }

  return (
    <section className={styles.panel}>
      <div className={styles.header}>
        <div>
          <h4 className={styles.title}>Suspected duplicate review</h4>
          <p className={styles.help}>
            Computed candidates are evidence only. This panel never merges events, confirms identity, or changes statistics.
          </p>
        </div>
        <button type="button" className={styles.closeBtn} onClick={() => setOpen(false)}>
          Close
        </button>
      </div>

      {error && <p className={styles.error}>! {error}</p>}
      {notice && <p className={styles.notice}>✓ {notice}</p>}
      {busy && <p className={styles.help}>Loading...</p>}

      {preview && (
        <div className={styles.section}>
          <div className={styles.sectionHead}>
            <strong>Computed candidates ({preview.total})</strong>
            <button
              type="button"
              className={styles.linkBtn}
              disabled={busy}
              onClick={() => setReconcileArmed(true)}
            >
              Recalculate and review save effect
            </button>
          </div>
          {!preview.eligible && <p className={styles.help}>Candidate review unavailable: {preview.reason}</p>}
          {preview.eligible && preview.items.length === 0 && (
            <p className={styles.help}>No candidates match the current rule.</p>
          )}
          {reconcileArmed && (
            <div className={styles.confirmBox}>
              <p>
                This explicit action recalculates candidates and may save up to 20 unreviewed relations. It does not merge events or confirm identity, but it does create saved relation records ({reconcileEffect(20)}).
              </p>
              <button
                type="button"
                className={styles.primaryBtn}
                disabled={busy || !preview.eligible}
                onClick={() => void reconcile()}
              >
                Confirm save up to 20
              </button>
              <button
                type="button"
                className={styles.linkBtn}
                disabled={busy}
                onClick={() => setReconcileArmed(false)}
              >
                Cancel
              </button>
            </div>
          )}
          <ul className={styles.list}>
            {preview.items.map((candidate) => (
              <li key={candidate.event_id} className={styles.item}>
                <div>
                  <strong>{candidate.event_id}</strong>
                  <span className={styles.meta}>
                    {candidate.camera_id} · {candidate.ppe_type} · overlap {candidate.overlap_seconds}s
                  </span>
                </div>
                <button
                  type="button"
                  className={styles.linkBtn}
                  disabled={busy}
                  onClick={() => void inspect(candidate.event_id)}
                >
                  Inspect evidence
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {selectedPeer && evidence && (
        <div className={styles.evidence}>
          <strong>{selectedPeer} evidence</strong>
          <span className={styles.meta}>
            {evidence.eligible ? `fingerprint: ${evidence.fingerprint}` : `Save unavailable: ${evidence.reason}`}
          </span>
          {evidence.eligible && evidence.evidence && <EvidenceDetails evidence={evidence.evidence} />}
          <button
            type="button"
            className={styles.primaryBtn}
            disabled={busy || !evidence.eligible || !evidence.fingerprint}
            onClick={() => void save()}
          >
            Save this evidence explicitly
          </button>
        </div>
      )}

      <div className={styles.section}>
        <strong>Saved relation history ({savedTotal})</strong>
        {savedHasMore && <p className={styles.help}>Only the newest 20 records are shown.</p>}
        {saved.length === 0 && <p className={styles.help}>No saved relations.</p>}
        <ul className={styles.list}>
          {saved.map((relation) => (
            <li key={relation.relation_id} className={styles.item}>
              <div>
                <strong>{relation.event_a === eventId ? relation.event_b : relation.event_a}</strong>
                <span className={styles.meta}>
                  {relation.state}{relation.stale ? ' · stale' : ''} · {relation.saved_by}
                </span>
              </div>
              {relation.state === 'saved' && (
                <button
                  type="button"
                  className={styles.linkBtn}
                  disabled={busy}
                  onClick={() => void release(relation)}
                >
                  Release saved relation
                </button>
              )}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
