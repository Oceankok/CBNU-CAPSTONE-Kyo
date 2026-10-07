// Event status reflects the review lifecycle of a candidate event
// unreviewable: media could not be recovered, recorded separately from violations (#116)
export type EventStatus =
  | 'pending'
  | 'confirmed'
  | 'false_positive'
  | 'hold'
  | 'unreviewable';

export type PpeType = 'helmet' | 'vest' | 'all';

export type ReviewResult =
  | 'confirmed'
  | 'false_positive'
  | 'hold'
  | 'unreviewable';

export type ReviewReasonCode =
  | 'confirmed_no_helmet'
  | 'confirmed_no_vest'
  | 'confirmed_other'
  | 'false_positive_occlusion'
  | 'false_positive_angle'
  | 'false_positive_other'
  | 'hold_unclear'
  | 'hold_low_resolution'
  | 'hold_other'
  | 'source_missing'
  | 'source_corrupt'
  | 'clip_unavailable';

export type RedactionMode = 'enhanced' | 'scrfd';

export interface EventMedia {
  media_id: string;
  camera_id: string;
  kind: 'image' | 'video';
  role: 'thumbnail' | 'clip' | 'reference';
  status:
    | 'registered'
    | 'processing'
    | 'ready'
    | 'failed'
    | 'missing'
    | 'delete_pending'
    | 'deleted';
  redaction_status: 'unprocessed' | 'complete' | 'failed' | 'legacy_unverified';
  url: string | null;
  can_reprocess: boolean;
  reprocess_unavailable_reason?: string | null;
  source_expires_at?: number | null;
  failure_category?:
    | 'source_unavailable'
    | 'source_invalid'
    | 'redaction_failed'
    | 'cleanup_failed'
    | null;
  error_code: string | null;
  faces_detected: number | null;
  processed_frames: number | null;
  redaction_mode?: RedactionMode | 'legacy' | null;
  inference_backend?: string | null;
  processing_seconds?: number | null;
}

// Whether the event can be judged with the media that exists (GET /api/events/{id}/media)
export interface ReviewAvailability {
  state:
    | 'available'
    | 'awaiting_clip'
    | 'processing'
    | 'retryable_failure'
    | 'no_usable_media';
  can_review: boolean;
  can_mark_unreviewable: boolean;
  missing_media_count: number;
}

export interface EventRetention {
  event_id: string;
  decision: 'pending' | 'retain' | 'delete';
  consent_reference?: string | null;
  decided_by?: string | null;
  decided_at?: string | null;
  retain_until?: number | null;
  deleted_at?: string | null;
  version: number;
}

export interface EventReviewHistory extends EventReview {
  history_id: string;
  recorded_at: string;
}

export interface CandidateEvent {
  event_id: string;
  camera_id: string;
  zone_name: string;
  process_type: string;
  tracking_id: string;
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
  media?: EventMedia[];
  retention?: EventRetention;
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
  unreviewable_count?: number;
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

// --- Warning Broadcast Settings ---

export type BroadcastLanguage = 'ko' | 'en';

// Per-PPE-type message template with optional zone override
export interface BroadcastTemplate {
  ppe_type: Exclude<PpeType, 'all'>;
  zone_name: string; // empty string means "all zones"
  language: BroadcastLanguage;
  message: string;
}

// Top-level broadcast control settings saved/loaded via API
export interface BroadcastSettings {
  enabled: boolean;
  default_language: BroadcastLanguage;
  cooldown_sec: number;
  templates: BroadcastTemplate[];
}

// --- Auth ---

export type UserRole = 'admin' | 'worker';

// Logged-in user as returned by POST /api/auth/login
export interface Session {
  access_token: string;
  user_id: string;
  display_name: string;
  role: UserRole;
}

// --- Zone safety rules (issue #101) ---

// Zone PPE codes accepted by the backend (backend/zones/policy.py); labels live in ppe.ts
export type ZonePpe =
  | Exclude<PpeType, 'all'>
  | 'goggles'
  | 'gloves'
  | 'safety_shoes'
  | 'hearing_protection'
  | 'mask'
  | 'harness';

export interface ZoneRule {
  zone_name: string;
  required_ppe: ZonePpe[];
  rules: string[];
  updated_at?: string;
}

// --- Account management (issue #104) ---

// Account as listed for admins; never includes password data
export interface AppUser {
  user_id: string;
  display_name: string;
  role: UserRole;
  zone_name: string | null;
  is_active: boolean;
}

export interface NewUser {
  user_id: string;
  display_name: string;
  role: UserRole;
  zone_name: string | null;
  password: string;
}

// --- Field PCs (broadcast output nodes, #96/#97) ---

export interface FieldNode {
  node_id: string;
  name: string;
  is_active: number;
  last_seen_at: number | null; // unix seconds
  language: string | null;
  voice_id: string | null;
  online: boolean;
}

export interface FieldVoice {
  id: string;
  name: string;
  languages: string[];
}

export interface NodeVoices {
  items: FieldVoice[];
  checked_at: number | null;
  language: string | null;
  voice_id: string | null;
  installable_languages: string[];
}

export interface FieldCommand {
  command_id: string;
  kind: string;
  status: string;
  created_at: number;
  finished_at: number | null;
  payload: Record<string, unknown>;
  result: Record<string, unknown> | null;
}

// --- Worker education (GET /api/worker/education, #111) ---

export interface WorkerEducation {
  quarter: string | null;
  zone_name: string | null;
  items: {
    recommendation_id: string;
    ppe_type: string;
    zone_name: string;
    education_topic: string;
    material: string | null; // AI-generated, admin-approved text (not generated yet)
  }[];
  empty_reason: 'zone_unassigned' | 'recommendations_unavailable' | null;
}
