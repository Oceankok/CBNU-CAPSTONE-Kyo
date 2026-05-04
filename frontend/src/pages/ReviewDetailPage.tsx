import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import StatusBadge from '../components/StatusBadge';
import { MOCK_EVENTS } from '../mock';
import type { ReviewResult, ReviewReasonCode } from '../types';
import styles from './ReviewDetailPage.module.css';

// Reason code options grouped by the review result selection
const REASON_OPTIONS: Record<ReviewResult, { value: ReviewReasonCode; label: string }[]> = {
  confirmed: [
    { value: 'confirmed_no_helmet', label: '안전모 미착용 확인' },
    { value: 'confirmed_no_vest', label: '안전조끼 미착용 확인' },
  ],
  false_positive: [
    { value: 'false_positive_occlusion', label: '가림 현상 (오탐)' },
    { value: 'false_positive_angle', label: '촬영 각도 오류 (오탐)' },
  ],
  hold: [
    { value: 'hold_unclear', label: '영상 불명확' },
    { value: 'hold_low_resolution', label: '해상도 부족' },
  ],
};

export default function ReviewDetailPage() {
  const { event_id } = useParams<{ event_id: string }>();
  const navigate = useNavigate();

  const eventIndex = MOCK_EVENTS.findIndex((e) => e.event_id === event_id);
  const event = MOCK_EVENTS[eventIndex];

  const [reviewResult, setReviewResult] = useState<ReviewResult | null>(null);
  const [reasonCode, setReasonCode] = useState<ReviewReasonCode | ''>('');
  const [comment, setComment] = useState('');
  const [secondReview, setSecondReview] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  if (!event) {
    return (
      <div className={styles.notFound}>
        <p>이벤트를 찾을 수 없습니다. (ID: {event_id})</p>
        <button onClick={() => navigate('/review')}>목록으로 돌아가기</button>
      </div>
    );
  }

  // Next event in the list for sequential review workflow
  const nextEvent = eventIndex < MOCK_EVENTS.length - 1 ? MOCK_EVENTS[eventIndex + 1] : null;

  const handleResultClick = (result: ReviewResult) => {
    setReviewResult(result);
    setReasonCode(''); // reset reason when the main result changes
  };

  const handleSubmit = () => {
    if (!reviewResult || !reasonCode) return;
    // In production: POST /api/reviews/{event_id} with ReviewRequest body
    console.log({ event_id, reviewResult, reasonCode, comment, secondReview });
    setSubmitted(true);
  };

  const goToNext = () => {
    if (!nextEvent) return;
    setReviewResult(null);
    setReasonCode('');
    setComment('');
    setSecondReview(false);
    setSubmitted(false);
    navigate(`/review/${nextEvent.event_id}`);
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
              <img src={event.thumbnail_path} alt="이벤트 썸네일" />
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
              <dt>클립 경로</dt>
              <dd className={styles.clipPath}>{event.video_clip_path || '—'}</dd>
            </dl>
          </div>
        </div>

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
              <p className={styles.submittedMsg}>✅ 검토가 제출되었습니다.</p>
              <div className={styles.actionRow}>
                <button className={styles.secondaryBtn} onClick={() => navigate('/review')}>
                  ← 목록으로
                </button>
                {nextEvent && (
                  <button className={styles.primaryBtn} onClick={goToNext}>
                    다음 이벤트 →
                  </button>
                )}
              </div>
            </div>
          ) : (
            <div className={styles.card}>
              <h4 className={styles.cardTitle}>검토 입력</h4>

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

              <div className={styles.actionRow}>
                <button className={styles.secondaryBtn} onClick={() => navigate('/review')}>
                  취소
                </button>
                {nextEvent && (
                  <button className={styles.secondaryBtn} onClick={goToNext}>
                    다음 이벤트 →
                  </button>
                )}
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
