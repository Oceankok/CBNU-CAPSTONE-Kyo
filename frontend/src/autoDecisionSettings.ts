import type { AutoDecisionSettings, AutoDecisionUpdate } from './api/autoDecisions';

// A missing/invalid readiness response must not offer an activation path.
export function maySelectEnforce(settings: AutoDecisionSettings | null): boolean {
  void settings;
  return false; // This management phase offers OFF/shadow only, regardless of saved blockers.
}

export function settingsSaveBlocker(settings: AutoDecisionSettings, conflict: boolean, reason: string): string | null {
  if (conflict) return '최신 서버 설정을 명시적으로 선택한 뒤 다시 편집해 주세요.';
  if (settings.mode === 'enforce') return '현재 활성 상태는 조회만 가능합니다. 저장하려면 OFF 또는 shadow를 선택하세요.';
  if (!reason.trim()) return '변경 사유가 필요합니다.';
  if (!Number.isFinite(settings.threshold) || settings.threshold < 0 || settings.threshold > 1)
    return '임계값은 0~1의 유한수여야 합니다.';
  if (settings.decision_method === 'continuous_rules_v1') {
    const criteria = settings.rule_criteria;
    if ((!criteria && settings.mode !== 'off') || (criteria && (!Number.isFinite(criteria.minimum_observed_seconds) || criteria.minimum_observed_seconds < 3 || criteria.minimum_observed_seconds > 3600 || !Number.isInteger(criteria.minimum_frame_samples) || criteria.minimum_frame_samples < 2 || criteria.minimum_frame_samples > 100000)))
      return '규칙의 관측 시간(3~3600초)과 샘플 수(2~100000 정수)를 명시하세요.';
    if (settings.ppe_types.some(value => !['helmet', 'vest'].includes(value))) return '연속 관측 규칙은 helmet/vest만 지원합니다.';
  }
  if (settings.mode !== 'off' && (!settings.model_versions.length || !settings.ppe_types.length || !settings.camera_ids.length))
    return '모델·PPE·카메라 적용 대상을 지정해 주세요.';
  return null;
}

export function settingsUpdate(settings: AutoDecisionSettings, conflict: boolean, reason: string): AutoDecisionUpdate {
  const blocked = settingsSaveBlocker(settings, conflict, reason);
  if (blocked) throw new Error(blocked);
  // Do not echo server-only activation metadata or replace the version after 409.
  return { expected_version: settings.version, mode: settings.mode, threshold: settings.threshold,
    decision_method: settings.decision_method, rule_criteria: settings.rule_criteria ? { ...settings.rule_criteria } : null,
    model_versions: [...settings.model_versions], ppe_types: [...settings.ppe_types],
    camera_ids: [...settings.camera_ids], zone_names: [...settings.zone_names],
    validation_reference: settings.validation_reference, reason: reason.trim() };
}

// Diagnostic requests retain exactly the edited policy scope, not only score fields.
export function settingsPreview(settings: AutoDecisionSettings, eventIds: string[]) {
  const blocked = settingsSaveBlocker(settings, false, 'diagnostic');
  if (blocked) throw new Error(blocked);
  return {event_ids: [...eventIds], mode: settings.mode, threshold: settings.threshold,
    decision_method: settings.decision_method, rule_criteria: settings.rule_criteria ? { ...settings.rule_criteria } : null,
    model_versions: [...settings.model_versions], ppe_types: [...settings.ppe_types],
    camera_ids: [...settings.camera_ids], zone_names: [...settings.zone_names],
    validation_reference: settings.validation_reference};
}
