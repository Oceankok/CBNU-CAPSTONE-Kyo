import { useState } from 'react';
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
import styles from './SuspectedDuplicatePanel.module.css';

const relationError = (e: unknown) =>
  e instanceof Error ? e.message : String(e);

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

  const load = async () => {
    const [nextPreview, nextSaved] = await Promise.all([
      fetchSuspectedDuplicatesPreview(eventId),
      fetchSavedSuspectedRelations(eventId),
    ]);
    setPreview(nextPreview);
    setSaved(nextSaved.items);
    setSavedTotal(nextSaved.total);
    setSavedHasMore(nextSaved.has_more);
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
    setSelectedPeer(peerId);
    setEvidence(null);
    setError(null);
    try {
      setEvidence(await fetchRelationEvidence(eventId, peerId));
    } catch (e) {
      setError(relationError(e));
    }
  };

  const save = async () => {
    if (!selectedPeer || !evidence?.fingerprint || !evidence.eligible) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await saveSuspectedRelation(eventId, selectedPeer, evidence.fingerprint);
      await load();
      setNotice(
        '현재 근거를 명시적으로 저장했습니다. 사건을 병합하거나 통계를 변경하지 않습니다.',
      );
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        try {
          setEvidence(await fetchRelationEvidence(eventId, selectedPeer));
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
      const result = (await reconcileSuspectedRelations(eventId)) as {
        created_count?: number;
      };
      await load();
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
            <button type="button" className={styles.linkBtn} disabled={busy} onClick={reconcile}>
              현재 근거 다시 확인
            </button>
          </div>
          {!preview.eligible && <p className={styles.help}>후보를 계산할 수 없습니다: {preview.reason}</p>}
          {preview.eligible && preview.items.length === 0 && (
            <p className={styles.help}>현재 조건에 맞는 후보가 없습니다.</p>
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
