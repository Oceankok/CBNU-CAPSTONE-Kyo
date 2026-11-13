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
import {
  isLatestRequest,
  matchesEvidenceSelection,
  reconcileEffect,
} from '../api/schema31Contracts';
import {
  executeConfirmedReconcile,
  acceptLatestResponse,
  fetchLatestEvidence,
  saveEvidenceWithConflictRecovery,
} from '../api/schema31Interactions';
import styles from './SuspectedDuplicatePanel.module.css';

const relationError = (e: unknown) =>
  e instanceof Error ? e.message : String(e);

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
        <dt>판정 규칙</dt>
        <dd>{textValue(evidence.rule_version)}</dd>
      </div>
      <div>
        <dt>시간 기준</dt>
        <dd>
          {textValue(evidence.interval_basis)} · 허용 오차 {textValue(evidence.clock_tolerance_seconds)}초
        </dd>
      </div>
      <div>
        <dt>공통 카메라 그룹 snapshot</dt>
        <dd>
          {groups.length
            ? groups
                .map((group) => {
                  const item = group as Record<string, unknown>;
                  return `${textValue(item.group_id)} (v${textValue(item.group_version)})`;
                })
                .join(', ')
            : '없음'}
        </dd>
      </div>
      <div>
        <dt>사건 관찰 구간</dt>
        <dd>
          {events.length
            ? events
                .map((event) => {
                  const item = event as Record<string, unknown>;
                  return `${textValue(item.event_id)}: ${textValue(item.timestamp_start)} ~ ${textValue(item.timestamp_end)}`;
                })
                .join(' · ')
            : '없음'}
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
  const [preview, setPreview] = useState<SuspectedDuplicatePreview | null>(
    null,
  );
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

  const load = async () => {
    const request = ++loadRequest.current;
    const [nextPreview, nextSaved] = await Promise.all([
      fetchSuspectedDuplicatesPreview(eventId),
      fetchSavedSuspectedRelations(eventId),
    ]);
    const result = acceptLatestResponse(request, loadRequest.current, {
      preview: nextPreview,
      saved: nextSaved,
    });
    if (!result) return;
    setPreview(result.preview);
    setSaved(result.saved.items);
    setSavedTotal(result.saved.total);
    setSavedHasMore(result.saved.has_more);
  };

  const openPanel = () => {
    setOpen(true);
    setBusy(true);
    setError(null);
    void load()
      .catch((e) => setError(relationError(e)))
      .finally(() => setBusy(false));
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
    } catch (e) {
      if (isLatestRequest(request, evidenceRequest.current)) setError(relationError(e));
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
        if (
          isLatestRequest(requestAtSave, evidenceRequest.current) &&
          selectedPeer === peerAtSave
        ) {
          setEvidence(saveResult.evidence);
          setError('근거가 바뀌었습니다. 새 근거를 확인한 뒤 다시 저장할지 판단하세요.');
        }
        return;
      }
      await load();
      if (
        !isLatestRequest(requestAtSave, evidenceRequest.current) ||
        !matchesEvidenceSelection(
          selectedPeer,
          peerAtSave,
          evidence?.fingerprint,
          fingerprintAtSave,
        )
      ) {
        return;
      }
      setNotice(
        '현재 근거를 명시적으로 저장했습니다. 사건을 병합하거나 통계를 변경하지 않습니다.',
      );
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        try {
          const nextEvidence = await fetchRelationEvidence(eventId, peerAtSave);
          if (
            isLatestRequest(requestAtSave, evidenceRequest.current) &&
            selectedPeer === peerAtSave
          ) {
            setEvidence(nextEvidence);
          }
          setError(
            '근거가 바뀌었습니다. 새 근거를 확인한 뒤 다시 저장할지 판단하세요.',
          );
        } catch (refreshError) {
          setError(`근거가 바뀌었고 새 근거를 불러오지 못했습니다: ${relationError(refreshError)}`);
        }
      } else {
        setError(relationError(e));
      }
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
      await load();
      setNotice('저장된 관계 기록을 해제했습니다. 계산된 의심 표시는 계속 보일 수 있습니다.');
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 409
          ? '저장 기록의 근거가 바뀌었습니다. 최신 기록을 다시 확인하세요.'
          : relationError(e),
      );
      if (e instanceof ApiError && e.status === 409) void load().catch(() => undefined);
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
      )) as {
        created_count?: number;
      };
      await load();
      setReconcileArmed(false);
      setNotice(
        `현재 근거를 명시적으로 다시 확인했습니다. 새로 저장된 관계 ${result.created_count ?? 0}건.`,
      );
    } catch (e) {
      setError(relationError(e));
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button type="button" className={styles.openBtn} onClick={openPanel}>
        의심 관계 확인
        {tag?.candidate_count ? ` (${tag.candidate_count})` : ''}
      </button>
    );
  }

  return (
    <section className={styles.panel}>
      <div className={styles.header}>
        <div>
          <h4 className={styles.title}>동일 사건 의심 관계</h4>
          <p className={styles.help}>
            계산된 메타데이터 후보와 저장한 evidence 관계는 별개입니다. 자동 병합·동일인 확정·통계 감산은 하지 않습니다.
          </p>
        </div>
        <button type="button" className={styles.closeBtn} onClick={() => setOpen(false)}>
          닫기
        </button>
      </div>

      {error && <p className={styles.error}>⚠ {error}</p>}
      {notice && <p className={styles.notice}>✓ {notice}</p>}
      {busy && <p className={styles.help}>불러오는 중...</p>}

      {preview && (
        <div className={styles.section}>
          <div className={styles.sectionHead}>
            <strong>계산 후보 ({preview.total})</strong>
            <button type="button" className={styles.linkBtn} disabled={busy} onClick={() => setReconcileArmed(true)}>
              현재 근거 다시 확인
            </button>
          </div>
          {!preview.eligible && <p className={styles.help}>후보를 계산할 수 없습니다: {preview.reason}</p>}
          {preview.eligible && preview.items.length === 0 && (
            <p className={styles.help}>현재 조건에 맞는 후보가 없습니다.</p>
          )}
          {reconcileArmed && (
            <div className={styles.confirmBox}>
              <p>
                이 작업은 현재 후보를 다시 계산하고, 아직 검토 이력이 없는 후보를 최대 20건까지
                저장합니다. 자동 병합이나 동일인 확정은 하지 않지만, 저장 기록은 생성됩니다.
                ({reconcileEffect(20)})
              </p>
              <button
                type="button"
                className={styles.primaryBtn}
                disabled={busy || !preview.eligible}
                onClick={() => void reconcile()}
              >
                최대 20건 저장 실행
              </button>
              <button
                type="button"
                className={styles.linkBtn}
                disabled={busy}
                onClick={() => setReconcileArmed(false)}
              >
                취소
              </button>
            </div>
          )}
          <ul className={styles.list}>
            {preview.items.map((candidate) => (
              <li key={candidate.event_id} className={styles.item}>
                <div>
                  <strong>{candidate.event_id}</strong>
                  <span className={styles.meta}>
                    {candidate.camera_id} · {candidate.ppe_type} · 겹침 {candidate.overlap_seconds}초
                  </span>
                </div>
                <button
                  type="button"
                  className={styles.linkBtn}
                  disabled={busy}
                  onClick={() => void inspect(candidate.event_id)}
                >
                  근거 보기
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {selectedPeer && evidence && (
        <div className={styles.evidence}>
          <strong>{selectedPeer} 현재 근거</strong>
          <span className={styles.meta}>
            {evidence.eligible ? `fingerprint: ${evidence.fingerprint}` : `저장 불가: ${evidence.reason}`}
          </span>
          {evidence.eligible && evidence.evidence && (
            <EvidenceDetails evidence={evidence.evidence} />
          )}
          <button
            type="button"
            className={styles.primaryBtn}
            disabled={busy || !evidence.eligible || !evidence.fingerprint}
            onClick={() => void save()}
          >
            이 근거를 명시적으로 저장
          </button>
        </div>
      )}

      <div className={styles.section}>
        <strong>저장·해제 이력 ({savedTotal})</strong>
        {savedHasMore && (
          <p className={styles.help}>최근 20건만 표시됩니다. 더 오래된 이력은 서버 목록에서 페이지로 조회하세요.</p>
        )}
        {saved.length === 0 && <p className={styles.help}>저장된 관계가 없습니다.</p>}
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
                <button type="button" className={styles.linkBtn} disabled={busy} onClick={() => void release(relation)}>
                  저장 기록 해제
                </button>
              )}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
