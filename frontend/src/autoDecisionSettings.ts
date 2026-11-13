import type { AutoDecisionSettings, AutoDecisionUpdate } from './api/autoDecisions';

// A missing/invalid readiness response must not offer an activation path.
export function maySelectEnforce(settings: AutoDecisionSettings | null): boolean {
  return !!settings && Array.isArray(settings.activation_blockers)
    && settings.activation_blockers.length === 0;
}

export function settingsSaveBlocker(settings: AutoDecisionSettings, conflict: boolean, reason: string): string | null {
  if (conflict) return '최신 서버 설정을 명시적으로 선택한 뒤 다시 편집해 주세요.';
  if (!reason.trim()) return '변경 사유가 필요합니다.';
  if (!Number.isFinite(settings.threshold) || settings.threshold < 0 || settings.threshold > 1)
    return '임계값은 0~1의 유한수여야 합니다.';
  if (settings.mode !== 'off' && (!settings.model_versions.length || !settings.ppe_types.length || !settings.camera_ids.length))
    return '모델·PPE·카메라 적용 대상을 지정해 주세요.';
  if (settings.mode === 'enforce' && !maySelectEnforce(settings))
    return '검증 또는 서버 준비 상태가 확인되지 않아 활성화할 수 없습니다.';
  return null;
}

export function settingsUpdate(settings: AutoDecisionSettings, conflict: boolean, reason: string): AutoDecisionUpdate {
  const blocked = settingsSaveBlocker(settings, conflict, reason);
  if (blocked) throw new Error(blocked);
  // Do not echo server-only activation metadata or replace the version after 409.
  return { expected_version: settings.version, mode: settings.mode, threshold: settings.threshold,
    model_versions: [...settings.model_versions], ppe_types: [...settings.ppe_types],
    camera_ids: [...settings.camera_ids], zone_names: [...settings.zone_names],
    validation_reference: settings.validation_reference, reason: reason.trim() };
}

// Diagnostic requests retain exactly the edited policy scope, not only score fields.
export function settingsPreview(settings: AutoDecisionSettings, eventIds: string[]) {
  return {event_ids: [...eventIds], mode: settings.mode, threshold: settings.threshold,
    model_versions: [...settings.model_versions], ppe_types: [...settings.ppe_types],
    camera_ids: [...settings.camera_ids], zone_names: [...settings.zone_names],
    validation_reference: settings.validation_reference};
}
