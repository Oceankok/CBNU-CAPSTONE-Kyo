import type { AutoDecisionSettings } from './api/autoDecisions';

export function modeLabel(mode: AutoDecisionSettings['mode'], active: boolean) {
  if (active) return '자동 확정 중';
  if (mode === 'shadow') return '시험 모드';
  if (mode === 'enforce') return '자동 확정 대기';
  return '꺼짐';
}

export function methodLabel(method: AutoDecisionSettings['decision_method'] | undefined) {
  return method === 'continuous_rules_v1' ? '연속 관측 규칙' : '모델 점수 확인';
}

const REASONS: Record<string, string> = {
  explicit_scope_required: '적용할 모델, 보호구, 카메라를 지정해야 합니다.',
  approved_validation_reference_required: '승인된 검증 기록이 필요합니다.',
  explicit_rule_criteria_required: '최소 관측 시간과 프레임 수를 설정해야 합니다.',
  validated_model_adapter_unavailable: '검증된 점수 계산 기능을 사용할 수 없습니다.',
  already_reviewed_or_not_pending: '검토 대기 상태의 사건만 자동 확정할 수 있습니다.',
  submission_provenance_missing: '최초 탐지 근거가 기록되어 있지 않습니다.',
  review_only: '관리자 검토용으로 수집된 사건입니다.',
  verified_confidence_missing: '판정에 사용할 검증된 점수가 없습니다.',
  model_out_of_scope: '이 사건의 모델은 적용 대상에 포함되지 않습니다.',
  ppe_out_of_scope: '이 보호구는 적용 대상에 포함되지 않습니다.',
  camera_out_of_scope: '이 카메라는 적용 대상에 포함되지 않습니다.',
  zone_out_of_scope: '이 구역은 적용 대상에 포함되지 않습니다.',
  merged_event_requires_review: '병합된 사건은 관리자 검토가 필요합니다.',
  media_deleted_or_expired: '영상이 삭제됐거나 보관 기간이 지났습니다.',
  observation_interval_unverified: '관측 시작과 종료 시각을 확인할 수 없습니다.',
  primary_redacted_video_missing: '얼굴 가림 처리가 완료된 사건 영상이 필요합니다.',
  evidence_incomplete: '영상 근거가 충분하지 않습니다.',
  requested_evidence_missing: '요청한 영상 중 아직 확보되지 않은 자료가 있습니다.',
  manual_review_evidence_not_automatic: '수동 검토용으로 수집된 영상입니다.',
  rule_ppe_not_supported: '이 보호구에는 연속 관측 규칙을 적용할 수 없습니다.',
  tracked_absence_observation_required: '같은 사람의 보호구 미착용을 추적한 기록이 필요합니다.',
  continuous_event_not_closed: '사건 관측이 정상적으로 마감되지 않았습니다.',
  closed_continuous_event_required: '사건 관측이 정상적으로 마감되지 않았습니다.',
  continuous_observation_incomplete: '미착용 판정이 중간에 끊긴 구간이 있습니다.',
  continuous_evidence_incomplete: '사건의 전체 영상 근거를 확보하지 못했습니다.',
  whole_event_evidence_incomplete: '사건의 전체 영상 근거를 확보하지 못했습니다.',
  abnormal_event_close_requires_review: '종료 사유를 관리자가 확인해야 합니다.',
  minimum_observed_seconds_not_met: '미착용 관측 시간이 기준보다 짧습니다.',
  minimum_frame_samples_not_met: '미착용이 확인된 프레임 수가 기준보다 적습니다.',
  suspected_duplicate_requires_review: '다른 카메라에서 같은 사건이 기록됐을 수 있습니다.',
};

export function decisionReason(code: string) {
  return Object.prototype.hasOwnProperty.call(REASONS, code) ? REASONS[code] : '세부 판정 기록을 확인하세요.';
}

const CLOSE_LABELS: Record<string, string> = {
  ppe_present: '보호구 재착용 확인',
  track_departed: '촬영 구역을 벗어남',
  primary_source_ended: '카메라 촬영 종료',
  operator_stop: '촬영 중지',
  observation_gap: '관측 중단',
  ppe_present_after_unknown: '판정 공백 이후 재착용 확인',
};

export function closeLabel(reason: string | null | undefined) {
  if (!reason) return '미확인';
  return Object.prototype.hasOwnProperty.call(CLOSE_LABELS, reason) ? CLOSE_LABELS[reason] : '세부 기록에서 확인';
}
