import type { AutoDecisionSettings, AutoDecisionUpdate } from './api/autoDecisions';

// A missing/invalid readiness response must not offer an activation path.
export function maySelectEnforce(settings: AutoDecisionSettings | null): boolean {
  void settings;
  return false; // This management phase offers OFF/shadow only, regardless of saved blockers.
}

export function settingsSaveBlocker(settings: AutoDecisionSettings, conflict: boolean, reason: string): string | null {
  if (conflict) return '최신 설정을 확인하고 현재 입력을 유지할지 선택하세요.';
  if (settings.mode === 'enforce') return '이 화면에서 저장하려면 꺼짐 또는 시험 모드를 선택하세요.';
  if (!reason.trim()) return '변경 사유를 입력하세요.';
  if (!Number.isFinite(settings.threshold) || settings.threshold < 0 || settings.threshold > 1)
    return '판정에 사용할 점수를 0부터 1까지 입력하세요.';
  if (settings.decision_method === 'continuous_rules_v1') {
    const criteria = settings.rule_criteria;
    if ((!criteria && settings.mode !== 'off') || (criteria && (!Number.isFinite(criteria.minimum_observed_seconds) || criteria.minimum_observed_seconds < 3 || criteria.minimum_observed_seconds > 3600 || !Number.isInteger(criteria.minimum_frame_samples) || criteria.minimum_frame_samples < 2 || criteria.minimum_frame_samples > 100000)))
      return '관측 시간은 3~3600초, 프레임 수는 2~100,000 사이의 정수로 입력하세요.';
    if (settings.ppe_types.some(value => !['helmet', 'vest'].includes(value))) return '연속 관측 규칙에는 안전모(helmet)와 조끼(vest)를 지정할 수 있습니다.';
  }
  if (settings.mode !== 'off' && (!settings.model_versions.length || !settings.ppe_types.length || !settings.camera_ids.length))
    return '적용할 모델, 보호구, 카메라를 지정하세요.';
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
