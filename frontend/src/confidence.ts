import type { CandidateEvent } from './types';

// Review-only events (#206) are suspected violations the model could not confirm.
// They have no measured confidence (ai_confidence is null); it must never be shown or filtered as 0%.
export const isReviewOnly = (
  e: Pick<CandidateEvent, 'review_only' | 'ai_confidence'>,
): boolean => e.review_only === true || e.ai_confidence == null;

export const REVIEW_NEEDED_LABEL = '검토 필요';
const REVIEW_NEEDED_HINT =
  'AI가 미착용을 확정하지 못해 관리자 판단이 필요한 후보입니다. 신뢰도는 산정하지 않았습니다.';
const CONFIDENCE_UNAVAILABLE_LABEL = '점수 없음';
const CONFIDENCE_UNAVAILABLE_HINT = '이 사건의 AI 신뢰도 점수는 산정하지 않았습니다.';

type EventConfidence = Pick<CandidateEvent, 'review_only' | 'ai_confidence' | 'event_status'>;

export function confidenceHint(event: EventConfidence): string {
  return event.event_status === 'pending' ? REVIEW_NEEDED_HINT : CONFIDENCE_UNAVAILABLE_HINT;
}

export type ConfidenceLevel = 'high' | 'mid' | 'low';

export const confidenceLevel = (c: number): ConfidenceLevel =>
  c >= 0.85 ? 'high' : c >= 0.7 ? 'mid' : 'low';

// The label for an unmeasured value follows the current review status.
export function formatConfidence(
  e: EventConfidence,
  digits = 0,
): string {
  if (isReviewOnly(e) || e.ai_confidence == null) {
    return e.event_status === 'pending' ? REVIEW_NEEDED_LABEL : CONFIDENCE_UNAVAILABLE_LABEL;
  }
  return `${(e.ai_confidence * 100).toFixed(digits)}%`;
}

// Confidence slider: an unmeasured event is never hidden by a threshold, because a
// missing confidence is not a low one and the event still needs a human decision
export const passesConfidenceFilter = (
  e: Pick<CandidateEvent, 'review_only' | 'ai_confidence'>,
  min: number,
): boolean => isReviewOnly(e) || (e.ai_confidence ?? 0) >= min;
