import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import StatusBadge from '../components/StatusBadge';
import EventMediaPanel from '../components/EventMediaPanel';
import { fetchEvent, submitReview, updateReview } from '../api/events';
import { getSession } from '../api/auth';
import type {
  CandidateEvent,
  EventReview,
  EventReviewHistory,
  ReviewResult,
  ReviewReasonCode,
  ReviewRequest,
  ReviewAvailability,
} from '../types';
import styles from './ReviewDetailPage.module.css';

// Flatten all reason options into a lookup map for display (code → Korean label)
const REASON_LABEL: Record<string, string> = {};

// Reason code options grouped by the review result selection
const REASON_OPTIONS: Record<
  ReviewResult,
  { value: ReviewReasonCode; label: string }[]
> = {
  confirmed: [
    { value: 'confirmed_no_helmet', label: '안전모 미착용 확인' },
    { value: 'confirmed_no_vest', label: '안전조끼 미착용 확인' },
    { value: 'confirmed_other', label: '기타 (확정)' },
  ],
  false_positive: [
    { value: 'false_positive_occlusion', label: '가림 현상 (오탐)' },
    { value: 'false_positive_angle', label: '촬영 각도 오류 (오탐)' },
    { value: 'false_positive_other', label: '기타 (오탐)' },
  ],
  hold: [
    { value: 'hold_unclear', label: '영상 불명확' },
    { value: 'hold_low_resolution', label: '해상도 부족' },
    { value: 'hold_other', label: '기타 (보류)' },
  ],
  unreviewable: [
    { value: 'source_missing', label: '원본 소실' },
    { value: 'source_corrupt', label: '원본 손상' },
    { value: 'clip_unavailable', label: '클립 확보 불가' },
  ],
};

const RESULT_LABEL: Record<ReviewResult, string> = {
  confirmed: '확정 위반',
  false_positive: '오탐',
  hold: '보류',
  unreviewable: '검토 불가',
};

// Why confirm / false-positive are locked (review_availability.state)
const AVAILABILITY_MSG: Record<ReviewAvailability['state'], string> = {
  available: '',
  awaiting_clip:
    '현장 PC에서 클립을 받는 중입니다. 자료가 도착한 뒤 판단할 수 있습니다. (보류는 가능)',
  processing:
    '얼굴 가림 처리 중입니다. 처리가 끝나면 판단할 수 있습니다. (보류는 가능)',
  retryable_failure:
    '자료 처리에 실패했습니다. 왼쪽 "자료 처리 정보"에서 재처리하거나 클립을 다시 받아야 합니다. (보류는 가능)',
  no_usable_media:
    '판단에 쓸 수 있는 자료가 없습니다. 자료를 복구할 수 없다면 "검토 불가"로 종결하세요.',
};

// Backend 409 codes on review submit
const SUBMIT_ERRORS: Record<string, string> = {
  usable_redacted_media_required:
    '얼굴 가림 처리된 자료가 있어야 확정·오탐으로 판단할 수 있습니다.',
  media_recovery_pending:
    '자료를 아직 복구할 수 있어 검토 불가로 종결할 수 없습니다. 재처리나 클립 수신을 먼저 확인하세요.',
};

// Populate the lookup map after REASON_OPTIONS is defined
for (const opts of Object.values(REASON_OPTIONS)) {
  for (const o of opts) REASON_LABEL[o.value] = o.label;
}

// Return human-readable Korean label for a stored reason code
function getReasonLabel(code: string): string {
  return REASON_LABEL[code] ?? code;
}

// Keyed by event id so moving between events starts from a fresh loading state
export default function ReviewDetailPage() {
  const { event_id } = useParams<{ event_id: string }>();
  return <ReviewDetail key={event_id} event_id={event_id} />;
}

function ReviewDetail({ event_id }: { event_id: string | undefined }) {
  const navigate = useNavigate();

  const [event, setEvent] = useState<CandidateEvent | null>(null);
  const [existingReview, setExistingReview] = useState<EventReview | null>(
    null,
  );
  const [reviewHistory, setReviewHistory] = useState<EventReviewHistory[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const [reviewResult, setReviewResult] = useState<ReviewResult | null>(null);
  const [reasonCode, setReasonCode] = useState<ReviewReasonCode | ''>('');
  const [comment, setComment] = useState('');
  const [secondReview, setSecondReview] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  // True when the user clicked '재검토 시작' to overwrite an existing hold/second-review
  const [isReReview, setIsReReview] = useState(false);
  // Submitted in this visit (reloadEvent then fills existingReview, so it can't tell us)
  const [justSubmitted, setJustSubmitted] = useState(false);
  // Reported by EventMediaPanel from the media API; null until loaded
  const [availability, setAvailability] = useState<ReviewAvailability | null>(
    null,
  );
  useEffect(() => {
    if (!event_id) return;
    fetchEvent(event_id)
      .then(({ event: ev, review, review_history = [] }) => {
        setEvent(ev);
        setReviewHistory(review_history);
        setExistingReview(review);
        // Already reviewed — show the existing result and lock the form
        setSubmitted(Boolean(review));
      })
      .catch((e) => setLoadError(e.message))
      .finally(() => setLoading(false));
  }, [event_id]);

  if (loading) {
    return (
      <div className={styles.notFound}>
        <p>이벤트를 불러오는 중...</p>
      </div>
    );
  }

  if (loadError || !event) {
    return (
      <div className={styles.notFound}>
        <p>
          이벤트를 찾을 수 없습니다. (ID: {event_id})
          {loadError ? ` — ${loadError}` : ''}
        </p>
        <button onClick={() => navigate('/review')}>목록으로 돌아가기</button>
      </div>
    );
  }

  const handleResultClick = (result: ReviewResult) => {
    setReviewResult(result);
    setReasonCode(''); // reset reason when the main result changes
  };

  const reloadEvent = async () => {
    if (!event_id) return;
    const data = await fetchEvent(event_id);
    setEvent(data.event);
    setExistingReview(data.review);
    setReviewHistory(data.review_history ?? []);
  };

  const handleSubmit = async () => {
    if (!reviewResult || !reasonCode || !event_id) return;
    if (reviewResult === 'unreviewable' && !comment.trim()) return;
    setSubmitError(null);
    const body: ReviewRequest = {
      // Backend should derive the reviewer from the token; sent for compatibility until it does
      reviewer_id: getSession()?.user_id ?? '',
      review_result: reviewResult,
      review_reason_code: reasonCode as ReviewReasonCode,
      review_comment: comment,
      second_review_needed:
        reviewResult === 'unreviewable' ? false : secondReview,
    };
    try {
      // Use PUT when overwriting an existing review (re-review flow)
      if (isReReview) {
        await updateReview(event_id, body);
      } else {
        await submitReview(event_id, body);
      }
      await reloadEvent();
      setJustSubmitted(true);
      setSubmitted(true);
      setIsReReview(false);
    } catch (e: unknown) {
      const message =
        e instanceof Error ? e.message : '제출 중 오류가 발생했습니다.';
      setSubmitError(SUBMIT_ERRORS[message] ?? message);
    }
  };

  // Unlock the form to allow re-reviewing a hold or second-review-needed event
  const handleStartReReview = () => {
    if (!existingReview) return;
    setReviewResult(existingReview.review_result);
    setReasonCode(existingReview.review_reason_code);
    setComment(existingReview.review_comment);
    setSecondReview(existingReview.second_review_needed);
    setIsReReview(true);
    setSubmitted(false);
    setSubmitError(null);
  };

  return (
    <div className={styles.page}>
      <div className={styles.breadcrumb}>
        <button className={styles.backBtn} onClick={() => navigate('/review')}>
          ← 목록
        </button>
        <span className={styles.eventLabel}>{event.event_id}</span>
        <StatusBadge status={event.event_status} />
      </div>

      <div className={styles.grid}>
        {/* Left: redacted media first, retention below (all cameras share one policy) */}
        <div className={styles.mediaCol}>
          <EventMediaPanel
            key={event.event_id}
            eventId={event.event_id}
            cameraId={event.camera_id}
            finalReview={Boolean(
              existingReview &&
              existingReview.review_result !== 'hold' &&
              !existingReview.second_review_needed,
            )}
            unreviewable={existingReview?.review_result === 'unreviewable'}
            onAvailability={setAvailability}
            onChange={reloadEvent}
          />
        </div>

        {/* Right: event details + review form */}
        <div className={styles.infoCol}>
          <div className={styles.card}>
            {/* Headline: what the reviewer is judging */}
            <p className={styles.headline}>
              {event.ppe_type === 'helmet' ? '안전모' : '안전조끼'} 미착용 의심
            </p>
            <p className={styles.headlineSub}>
              {event.zone_name} ·{' '}
              {new Date(event.timestamp_start).toLocaleString('ko-KR')}
            </p>
            <dl className={styles.dl}>
              <dt>AI 신뢰도</dt>
              <dd>
                <span
                  className={`${styles.confidenceValue} ${
                    event.ai_confidence >= 0.85
                      ? styles.high
                      : event.ai_confidence >= 0.7
                        ? styles.mid
                        : styles.low
                  }`}
                >
                  {(event.ai_confidence * 100).toFixed(1)}%
                </span>
              </dd>
              <dt>지속 시간</dt>
              <dd>{event.duration_sec}초</dd>
              <dt>공정</dt>
              <dd>{event.process_type}</dd>
            </dl>
            {/* Technical metadata — rarely needed for the decision */}
            <details className={styles.techDetails}>
              <summary>기술 정보</summary>
              <dl className={styles.dl}>
                <dt>종료 일시</dt>
                <dd>{new Date(event.timestamp_end).toLocaleString('ko-KR')}</dd>
                <dt>카메라 ID</dt>
                <dd>{event.camera_id}</dd>
                <dt>샘플 프레임 수</dt>
                <dd>{event.frame_sample_count}프레임</dd>
                <dt>모델 버전</dt>
                <dd>{event.model_version}</dd>
              </dl>
            </details>
          </div>

          {reviewHistory.length > 0 && (
            <details className={styles.card}>
              <summary>이전 검토 기록 ({reviewHistory.length}건)</summary>
              {reviewHistory.map((review) => (
                <p key={review.history_id}>
                  {review.review_time} · {review.reviewer_id} ·{' '}
                  {getReasonLabel(review.review_reason_code)}
                </p>
              ))}
            </details>
          )}
          {submitted ? (
            <div className={styles.submittedCard}>
              <p className={styles.submittedMsg}>
                {justSubmitted
                  ? '✅ 검토가 제출되었습니다.'
                  : '⚠ 이미 검토된 이벤트입니다.'}
              </p>
              {/* Show a summary of the review decision */}
              {(existingReview || reviewResult) && (
                <dl className={styles.reviewSummary}>
                  <dt>판단 결과</dt>
                  <dd>
                    {
                      RESULT_LABEL[
                        existingReview?.review_result ?? reviewResult ?? 'hold'
                      ]
                    }
                  </dd>
                  <dt>판단 사유</dt>
                  <dd>
                    {existingReview
                      ? getReasonLabel(existingReview.review_reason_code)
                      : getReasonLabel(reasonCode)}
                  </dd>
                  {(existingReview?.review_comment || comment) && (
                    <>
                      <dt>코멘트</dt>
                      <dd>
                        {existingReview
                          ? existingReview.review_comment
                          : comment}
                      </dd>
                    </>
                  )}
                  <dt>검토 시각</dt>
                  <dd>
                    {existingReview
                      ? existingReview.review_time
                      : new Date().toLocaleString('ko-KR')}
                  </dd>
                </dl>
              )}
              <div className={styles.actionRow}>
                <button
                  className={styles.secondaryBtn}
                  onClick={() => navigate('/review')}
                >
                  ← 목록으로
                </button>
                {/* Allow re-review only for hold or second-review-needed events.
                    !! converts to boolean to prevent React rendering numeric 0 as text */}
                {existingReview &&
                  !!(
                    event?.event_status === 'hold' ||
                    event?.event_status === 'unreviewable' ||
                    existingReview.second_review_needed
                  ) && (
                    <button
                      className={styles.primaryBtn}
                      onClick={handleStartReReview}
                    >
                      🔄 재검토 시작
                    </button>
                  )}
              </div>
            </div>
          ) : (
            <div className={styles.card}>
              <h4 className={styles.cardTitle}>
                {isReReview ? '🔄 재검토 입력' : '검토 입력'}
              </h4>

              {availability && !availability.can_review && (
                <p className={styles.blockedNote} role="status">
                  {AVAILABILITY_MSG[availability.state]}
                </p>
              )}

              {/* Result toggle; confirm / false positive need usable redacted media */}
              <div className={styles.resultButtons}>
                <button
                  className={`${styles.resultBtn} ${styles.confirmedBtn} ${reviewResult === 'confirmed' ? styles.activeConfirmed : ''}`}
                  onClick={() => handleResultClick('confirmed')}
                  disabled={availability?.can_review === false}
                >
                  ✓ 확정 위반
                </button>
                <button
                  className={`${styles.resultBtn} ${styles.falsePosBtn} ${reviewResult === 'false_positive' ? styles.activeFalsePos : ''}`}
                  onClick={() => handleResultClick('false_positive')}
                  disabled={availability?.can_review === false}
                >
                  ✕ 오탐
                </button>
                <button
                  className={`${styles.resultBtn} ${styles.holdBtn} ${reviewResult === 'hold' ? styles.activeHold : ''}`}
                  onClick={() => handleResultClick('hold')}
                >
                  ⏸ 보류
                </button>
                {(availability?.can_mark_unreviewable ||
                  reviewResult === 'unreviewable') && (
                  <button
                    className={`${styles.resultBtn} ${styles.unreviewableBtn} ${reviewResult === 'unreviewable' ? styles.activeUnreviewable : ''}`}
                    onClick={() => handleResultClick('unreviewable')}
                  >
                    ⊘ 검토 불가
                  </button>
                )}
              </div>

              {/* Reason code dropdown — options depend on the selected result */}
              <div className={styles.field}>
                <label className={styles.fieldLabel}>판단 사유</label>
                <select
                  className={styles.select}
                  value={reasonCode}
                  onChange={(e) =>
                    setReasonCode(e.target.value as ReviewReasonCode)
                  }
                  disabled={!reviewResult}
                >
                  <option value="">-- 사유 선택 --</option>
                  {reviewResult &&
                    REASON_OPTIONS[reviewResult].map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                </select>
              </div>

              <div className={styles.field}>
                <label className={styles.fieldLabel}>
                  검토 의견{' '}
                  <span className={styles.optional}>
                    {reviewResult === 'unreviewable'
                      ? '(필수 — 복구할 수 없는 이유)'
                      : '(선택)'}
                  </span>
                </label>
                <textarea
                  className={styles.textarea}
                  rows={3}
                  placeholder={
                    reviewResult === 'unreviewable'
                      ? '예: 현장 PC에서도 원본 복구가 불가능함을 확인했습니다.'
                      : '추가 의견을 입력하세요...'
                  }
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                />
              </div>

              {reviewResult !== 'unreviewable' && (
                <label className={styles.checkboxLabel}>
                  <input
                    type="checkbox"
                    checked={secondReview}
                    onChange={(e) => setSecondReview(e.target.checked)}
                  />
                  2차 검토 요청
                </label>
              )}

              {submitError && (
                <p style={{ color: '#e53e3e', marginTop: '0.5rem' }}>
                  ⚠ {submitError}
                </p>
              )}
              <div className={styles.actionRow}>
                <button
                  className={styles.secondaryBtn}
                  onClick={() => navigate('/review')}
                >
                  취소
                </button>
                <button
                  className={styles.primaryBtn}
                  disabled={
                    !reviewResult ||
                    !reasonCode ||
                    (reviewResult === 'unreviewable' && !comment.trim())
                  }
                  onClick={handleSubmit}
                >
                  검토 제출
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
