import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import ConfirmButton from './ConfirmButton';
import { fetchEvents, mergeEvents } from '../api/events';
import type { CandidateEvent } from '../types';
import styles from './EventMergePanel.module.css';

// Backend 409/422 codes on merge (#171)
const MERGE_ERRORS: Record<string, string> = {
  event_merge_version_conflict:
    '다른 곳에서 먼저 병합되었습니다. 화면을 새로고침한 뒤 다시 시도하세요.',
  unreviewed_event_required: '검토 전인 이벤트만 병합할 수 있습니다.',
  event_already_merged: '이미 다른 이벤트에 병합된 이벤트가 포함되어 있습니다.',
  undecided_retention_required:
    '보관 여부가 이미 정해진 이벤트는 병합할 수 없습니다.',
  event_history_prevents_merge:
    '검토·보관 이력이 있는 이벤트는 병합할 수 없습니다.',
  clip_delivery_pending:
    '클립을 받는 중인 이벤트가 있습니다. 도착하거나 확보 불가로 처리한 뒤 다시 시도하세요.',
  media_processing_pending:
    '얼굴 가림 처리 중인 자료가 있습니다. 처리가 끝난 뒤 다시 시도하세요.',
  matching_ppe_type_required: '같은 PPE 유형의 이벤트만 병합할 수 있습니다.',
  merge_target_cannot_be_source:
    '이미 다른 이벤트를 포함한 이벤트는 병합 대상이 될 수 없습니다.',
};

// Admin picks events that are the same incident and folds them into this one (#138)
export default function EventMergePanel({
  event,
  onMerged,
}: {
  event: CandidateEvent;
  onMerged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [candidates, setCandidates] = useState<CandidateEvent[] | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Loaded when first opened; only unreviewed events of the same PPE type can merge
  useEffect(() => {
    if (!open || candidates) return;
    fetchEvents()
      .then((r) =>
        setCandidates(
          r.items
            .filter(
              (e) =>
                e.event_id !== event.event_id &&
                e.event_status === 'pending' &&
                e.ppe_type === event.ppe_type,
            )
            .sort(
              (a, b) =>
                Math.abs(
                  +new Date(a.timestamp_start) -
                    +new Date(event.timestamp_start),
                ) -
                Math.abs(
                  +new Date(b.timestamp_start) -
                    +new Date(event.timestamp_start),
                ),
            ),
        ),
      )
      .catch((e: Error) => setError(e.message));
  }, [open, candidates, event]);

  const toggle = (id: string) =>
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));

  const submit = () => {
    setBusy(true);
    setError(null);
    mergeEvents(event.event_id, {
      source_event_ids: picked,
      expected_version: event.merge_version ?? 0,
      reason: reason.trim(),
    })
      .then(() => {
        setPicked([]);
        setReason('');
        setCandidates(null);
        setOpen(false);
        onMerged();
      })
      .catch((e: Error) => setError(MERGE_ERRORS[e.message] ?? e.message))
      .finally(() => setBusy(false));
  };

  if (!open) {
    return (
      <button
        type="button"
        className={styles.openBtn}
        onClick={() => setOpen(true)}
      >
        같은 사건의 다른 이벤트 합치기
      </button>
    );
  }

  return (
    <section className={styles.panel}>
      <h4 className={styles.title}>같은 사건의 다른 이벤트 합치기</h4>
      <p className={styles.help}>
        이 이벤트를 대표로 두고, 같은 사건이라고 확인한 이벤트를 합칩니다.
        합쳐진 이벤트의 영상은 이 이벤트의 참고 자료로 옮겨지며, 병합은 되돌릴
        수 없습니다. 검토 전이고 같은 PPE 유형인 이벤트만 선택할 수 있습니다.
      </p>

      {error && <p className={styles.error}>⚠ {error}</p>}
      {!candidates && !error && <p className={styles.help}>불러오는 중...</p>}
      {candidates?.length === 0 && (
        <p className={styles.help}>합칠 수 있는 이벤트가 없습니다.</p>
      )}

      {candidates && candidates.length > 0 && (
        <ul className={styles.list}>
          {candidates.map((c) => (
            <li key={c.event_id}>
              <label className={styles.item}>
                <input
                  type="checkbox"
                  checked={picked.includes(c.event_id)}
                  onChange={() => toggle(c.event_id)}
                />
                <span className={styles.itemMain}>
                  {c.event_id}
                  <span className={styles.itemSub}>
                    {c.camera_id} · {c.zone_name} ·{' '}
                    {new Date(c.timestamp_start).toLocaleString('ko-KR')}
                  </span>
                </span>
                <Link
                  to={`/review/${c.event_id}`}
                  target="_blank"
                  className={styles.viewLink}
                  onClick={(e) => e.stopPropagation()}
                >
                  영상 보기
                </Link>
              </label>
            </li>
          ))}
        </ul>
      )}

      <label className={styles.field}>
        <span className={styles.fieldLabel}>합치는 이유 (필수)</span>
        <textarea
          className={styles.textarea}
          rows={2}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="예: 두 카메라의 영상을 확인해 같은 작업자의 같은 장면으로 판단했습니다."
        />
      </label>

      <div className={styles.actions}>
        <button
          type="button"
          className={styles.cancel}
          onClick={() => setOpen(false)}
        >
          닫기
        </button>
        <ConfirmButton
          className={styles.submit}
          label={`${picked.length}건 합치기`}
          question="합친 뒤에는 되돌릴 수 없습니다."
          confirmLabel="합치기"
          danger={false}
          disabled={busy || picked.length === 0 || !reason.trim()}
          onConfirm={submit}
        />
      </div>
    </section>
  );
}
