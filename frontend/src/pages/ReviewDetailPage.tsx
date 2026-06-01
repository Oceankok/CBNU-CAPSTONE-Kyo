import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import StatusBadge from '../components/StatusBadge';
import { fetchEvent, submitReview, updateReview } from '../api/events';
import { mediaUrl } from '../api/client';
import type { CandidateEvent, EventReview, ReviewResult, ReviewReasonCode, ReviewRequest } from '../types';
import styles from './ReviewDetailPage.module.css';

// Flatten all reason options into a lookup map for display (code → Korean label)
const REASON_LABEL: Record<string, string> = {};

// Reason code options grouped by the review result selection
const REASON_OPTIONS: Record<ReviewResult, { value: ReviewReasonCode; label: string }[]> = {
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
};

// Populate the lookup map after REASON_OPTIONS is defined
for (const opts of Object.values(REASON_OPTIONS)) {
  for (const o of opts) REASON_LABEL[o.value] = o.label;
}

// Return human-readable Korean label for a stored reason code
function getReasonLabel(code: string): string {
  return REASON_LABEL[code] ?? code;
}

export default function ReviewDetailPage() {
  const { event_id } = useParams<{ event_id: string }>();
  const navigate = useNavigate();

  const [event, setEvent] = useState<CandidateEvent | null>(null);
  const [existingReview, setExistingReview] = useState<EventReview | null>(null);
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
  // Controls the full-screen video modal
  const [videoOpen, setVideoOpen] = useState(false);

  // Close modal on Escape key
  useEffect(() => {
    if (!videoOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setVideoOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [videoOpen]);

  useEffect(() => {
    if (!event_id) return;
    setLoading(true);
    fetchEvent(event_id)
      .then(({ event: ev, review }) => {
        setEvent(ev);
        if (review) {
          // Event already reviewed — show existing result and lock the form
          setExistingReview(review);
          setSubmitted(true);
        }
      })
      .catch((e) => setLoadError(e.message))
      .finally(() => setLoading(false));
  }, [event_id]);

  if (loading) {
    return <div className={styles.notFound}><p>이벤트를 불러오는 중...</p></div>;
  }

  if (loadError || !event) {
    return (
      <div className={styles.notFound}>
        <p>이벤트를 찾을 수 없습니다. (ID: {event_id}){loadError ? ` — ${loadError}` : ''}</p>
        <button onClick={() => navigate('/review')}>목록으로 돌아가기</button>
      </div>
    );
  }

  const handleResultClick = (result: ReviewResult) => {
    setReviewResult(result);
    setReasonCode(''); // reset reason when the main result changes
  };

  const handleSubmit = async () => {
    if (!reviewResult || !reasonCode || !event_id) return;
    setSubmitError(null);
    const body: ReviewRequest = {
      reviewer_id: 'admin01',
      review_result: reviewResult,
      review_reason_code: reasonCode as ReviewReasonCode,
      review_comment: comment,
      second_review_needed: secondReview,
    };
    try {
      // Use PUT when overwriting an existing review (re-review flow)
      if (isReReview) {
        await updateReview(event_id, body);
      } else {
        await submitReview(event_id, body);
      }
      setSubmitted(true);
      setIsReReview(false);
    } catch (e: unknown) {
      setSubmitError(e instanceof Error ? e.message : '제출 중 오류가 발생했습니다.');
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
        {/* Left: thumbnail placeholder + media metadata */}
        <div className={styles.mediaCol}>
          <div className={styles.thumbnail}>
            {event.thumbnail_path ? (
              <img src={mediaUrl(event.thumbnail_path)} alt="이벤트 썸네일" />
            ) : (
              <div className={styles.thumbnailPlaceholder}>
                <span className={styles.thumbnailIcon}>📷</span>
                <p>썸네일 미리보기</p>
                <small>{event.camera_id}</small>
              </div>
            )}
          </div>

          <div className={styles.card}>
            <h4 className={styles.cardTitle}>영상 정보</h4>
            <dl className={styles.dl}>
              <dt>카메라 ID</dt>
              <dd>{event.camera_id}</dd>
              <dt>샘플 프레임 수</dt>
              <dd>{event.frame_sample_count}프레임</dd>
              <dt>모델 버전</dt>
              <dd>{event.model_version}</dd>
              <dt>영상 클립</dt>
              <dd>
                {event.video_clip_path ? (
                  <button className={styles.clipBtn} onClick={() => setVideoOpen(true)}>
                    ▶ 클립 열기
                  </button>
                ) : '—'}
              </dd>
            </dl>
          </div>
        </div>

        {/* Video modal — blurred backdrop, closes on overlay click or Escape */}
        {videoOpen && event.video_clip_path && (
          <div
            className={styles.videoOverlay}
            onClick={() => setVideoOpen(false)}
            role="dialog"
            aria-modal="true"
          >
            <div className={styles.videoModal} onClick={(e) => e.stopPropagation()}>
              <button className={styles.videoCloseBtn} onClick={() => setVideoOpen(false)}>✕</button>
              <video
                src={mediaUrl(event.video_clip_path)}
                controls
                autoPlay
                className={styles.videoPlayer}
              />
            </div>
          </div>
        )}

        {/* Right: event details + review form */}
        <div className={styles.infoCol}>
          <div className={styles.card}>
            <h4 className={styles.cardTitle}>이벤트 정보</h4>
            <dl className={styles.dl}>
              <dt>발생 일시</dt>
              <dd>{new Date(event.timestamp_start).toLocaleString('ko-KR')}</dd>
              <dt>종료 일시</dt>
              <dd>{new Date(event.timestamp_end).toLocaleString('ko-KR')}</dd>
              <dt>지속 시간</dt>
              <dd>{event.duration_sec}초</dd>
              <dt>구역</dt>
              <dd>{event.zone_name}</dd>
              <dt>공정</dt>
              <dd>{event.process_type}</dd>
              <dt>PPE 유형</dt>
              <dd>{event.ppe_type === 'helmet' ? '안전모' : '안전조끼'}</dd>
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
            </dl>
          </div>

          {submitted ? (
            <div className={styles.submittedCard}>
              <p className={styles.submittedMsg}>
                {existingReview ? '⚠ 이미 검토된 이벤트입니다.' : '✅ 검토가 제출되었습니다.'}
              </p>
              {/* Show a summary of the review decision */}
              {(existingReview || reviewResult) && (
                <dl className={styles.reviewSummary}>
                  <dt>판단 결과</dt>
                  <dd>
                    {existingReview
                      ? existingReview.review_result === 'confirmed' ? '확정 위반'
                        : existingReview.review_result === 'false_positive' ? '오탐'
                        : '보류'
                      : reviewResult === 'confirmed' ? '확정 위반'
                        : reviewResult === 'false_positive' ? '오탐'
                        : '보류'}
                  </dd>
                  <dt>판단 사유</dt>
                  <dd>{existingReview ? getReasonLabel(existingReview.review_reason_code) : getReasonLabel(reasonCode)}</dd>
                  {(existingReview?.review_comment || comment) && (
                    <>
                      <dt>코멘트</dt>
                      <dd>{existingReview ? existingReview.review_comment : comment}</dd>
                    </>
                  )}
                  <dt>검토 시각</dt>
                  <dd>{existingReview ? existingReview.review_time : new Date().toLocaleString('ko-KR')}</dd>
                </dl>
              )}
              <div className={styles.actionRow}>
                <button className={styles.secondaryBtn} onClick={() => navigate('/review')}>
                  ← 목록으로
                </button>
                {/* Allow re-review only for hold or second-review-needed events.
                    !! converts to boolean to prevent React rendering numeric 0 as text */}
                {existingReview && !!(event?.event_status === 'hold' || existingReview.second_review_needed) && (
                  <button className={styles.primaryBtn} onClick={handleStartReReview}>
                    🔄 재검토 시작
                  </button>
                )}
              </div>
            </div>
          ) : (
            <div className={styles.card}>
              <h4 className={styles.cardTitle}>{isReReview ? '🔄 재검토 입력' : '검토 입력'}</h4>

              {/* Three-button toggle for review result */}
              <div className={styles.resultButtons}>
                <button
                  className={`${styles.resultBtn} ${styles.confirmedBtn} ${reviewResult === 'confirmed' ? styles.activeConfirmed : ''}`}
                  onClick={() => handleResultClick('confirmed')}
                >
                  ✓ 확정 위반
                </button>
                <button
                  className={`${styles.resultBtn} ${styles.falsePosBtn} ${reviewResult === 'false_positive' ? styles.activeFalsePos : ''}`}
                  onClick={() => handleResultClick('false_positive')}
                >
                  ✕ 오탐
                </button>
                <button
                  className={`${styles.resultBtn} ${styles.holdBtn} ${reviewResult === 'hold' ? styles.activeHold : ''}`}
                  onClick={() => handleResultClick('hold')}
                >
                  ⏸ 보류
                </button>
              </div>

              {/* Reason code dropdown — options depend on the selected result */}
              <div className={styles.field}>
                <label className={styles.fieldLabel}>판단 사유</label>
                <select
                  className={styles.select}
                  value={reasonCode}
                  onChange={(e) => setReasonCode(e.target.value as ReviewReasonCode)}
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
                  검토 의견 <span className={styles.optional}>(선택)</span>
                </label>
                <textarea
                  className={styles.textarea}
                  rows={3}
                  placeholder="추가 의견을 입력하세요..."
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                />
              </div>

              <label className={styles.checkboxLabel}>
                <input
                  type="checkbox"
                  checked={secondReview}
                  onChange={(e) => setSecondReview(e.target.checked)}
                />
                2차 검토 요청
              </label>

              {submitError && (
                <p style={{ color: '#e53e3e', marginTop: '0.5rem' }}>⚠ {submitError}</p>
              )}
              <div className={styles.actionRow}>
                <button className={styles.secondaryBtn} onClick={() => navigate('/review')}>
                  취소
                </button>
                <button
                  className={styles.primaryBtn}
                  disabled={!reviewResult || !reasonCode}
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
