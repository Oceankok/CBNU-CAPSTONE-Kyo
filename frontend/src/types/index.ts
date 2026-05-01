// Event status reflects the review lifecycle of a candidate event
export type EventStatus = 'pending' | 'confirmed' | 'false_positive' | 'hold';

export type PpeType = 'helmet' | 'vest' | 'all';

export type ReviewResult = 'confirmed' | 'false_positive' | 'hold';

export type ReviewReasonCode =
  | 'confirmed_no_helmet'
  | 'confirmed_no_vest'
  | 'false_positive_occlusion'
  | 'false_positive_angle'
  | 'hold_unclear'
  | 'hold_low_resolution';

export interface CandidateEvent {
  event_id: string;
  camera_id: string;
  zone_name: string;
  process_type: string;
  ppe_type: Exclude<PpeType, 'all'>;
  timestamp_start: string;
  timestamp_end: string;
  duration_sec: number;
  frame_sample_count: number;
  thumbnail_path: string;
  video_clip_path: string;
  ai_confidence: number;
  person_detected: boolean;
  ppe_detected: boolean;
  model_version: string;
  event_status: EventStatus;
}

export interface EventReview {
  review_id: string;
  event_id: string;
  reviewer_id: string;
  review_result: ReviewResult;
  review_reason_code: ReviewReasonCode;
  review_time: string;
  review_comment: string;
  confirmed_violation: boolean;
  second_review_needed: boolean;
}

export interface ReviewRequest {
  reviewer_id: string;
  review_result: ReviewResult;
  review_reason_code: ReviewReasonCode;
  review_comment: string;
  second_review_needed: boolean;
}

// Summary card values for the home/stats pages
export interface QuarterlySummary {
  quarter: string;
  candidate_count: number;
  confirmed_count: number;
  false_positive_count: number;
  hold_count: number;
}

export interface PpeTypeStat {
  ppe_type: Exclude<PpeType, 'all'>;
  confirmed_count: number;
  priority_score: number;
}

export interface ZoneStat {
  zone_name: string;
  confirmed_count: number;
  priority_score: number;
}

export interface TrendPoint {
  quarter: string;
  helmet: number;
  vest: number;
}

export interface QuarterlyStats {
  quarter: string;
  summary: QuarterlySummary;
  by_ppe_type: PpeTypeStat[];
  by_zone: ZoneStat[];
  trend: TrendPoint[];
}

export interface ScoreBreakdown {
  confirmed_count: number;
  repeat_weeks: number;
  zone_concentration: number;
  process_risk_weight: number;
}

export interface EducationRecommendation {
  recommendation_id: string;
  recommendation_rank: number;
  ppe_type: Exclude<PpeType, 'all'>;
  zone_name: string;
  education_topic: string;
  priority_score: number;
  score_breakdown: ScoreBreakdown;
  generated_at: string;
}

export interface EducationRecommendationList {
  quarter: string;
  generated_at: string;
  items: EducationRecommendation[];
}

// Shared filter state used across event list and stats pages
export interface FilterState {
  ppeType: PpeType;
  status: EventStatus | 'all';
  zone: string[];
  dateFrom: string;
  dateTo: string;
  minConfidence: number;
}
