import { apiFetch } from './client';

export interface RuleCriteria { minimum_observed_seconds: number; minimum_frame_samples: number }
export interface RuleEvaluation {
  method: 'continuous_rules_v1'; eligible: boolean; criteria: RuleCriteria | null;
  observed_seconds: number | null; frame_sample_count: number; gap_count: number | null;
  gap_seconds: number | null; close_reason: string | null; score: null;
  blocking_reasons: string[]; limitations: string[];
}
export interface DecisionDiagnostic {
  event_id: string; observed_confidence: number | null; threshold_candidate: boolean;
  blocking_reasons: string[]; evidence_fingerprint?: string;
  decision_method?: 'validated_model' | 'continuous_rules_v1'; rule_evaluation?: RuleEvaluation;
  settings_version?: number; source?: string;
}

export interface AutoDecisionSettings {
  version: number;
  mode: 'off' | 'shadow' | 'enforce';
  decision_method: 'validated_model' | 'continuous_rules_v1';
  rule_criteria: RuleCriteria | null;
  threshold: number;
  model_versions: string[];
  ppe_types: string[];
  camera_ids: string[];
  zone_names: string[];
  validation_reference: string | null;
  activation_blockers: string[];
  automatic_confirmation_enabled: boolean;
}
export type AutoDecisionUpdate = Omit<AutoDecisionSettings,
  'version' | 'activation_blockers' | 'automatic_confirmation_enabled'> & {
    expected_version: number; reason: string;
  };
export interface DecisionPreview {
  automatic_confirmation_enabled: false;
  persisted: false;
  items: DecisionDiagnostic[];
}
export const fetchAutoDecisionSettings = () =>
  apiFetch<AutoDecisionSettings>('/api/auto-decisions/settings');
export const saveAutoDecisionSettings = (body: AutoDecisionUpdate) =>
  apiFetch<AutoDecisionSettings>('/api/auto-decisions/settings', {
    method: 'PUT', body: JSON.stringify(body),
  });
export const previewAutoDecisions = (body: Omit<AutoDecisionUpdate, 'expected_version' | 'reason'> & { event_ids: string[] }) => apiFetch<DecisionPreview>('/api/auto-decisions/settings/preview', {
  method: 'POST', body: JSON.stringify(body),
});

export interface AutoDecisionHistory {
  items: Array<{ version: number; policy: Omit<AutoDecisionUpdate, 'expected_version' | 'reason'>;
    changed_by: string; changed_at: string; reason: string }>;
  limit: number; offset: number;
}
export const fetchAutoDecisionHistory = (offset = 0) =>
  apiFetch<AutoDecisionHistory>(`/api/auto-decisions/settings/history?limit=20&offset=${offset}`);

export interface ShadowHistory {
  event_id: string; limit: number; offset: number;
  items: Array<{ run_id: string; actor_id: string; created_at: string;
    policy: Partial<Omit<AutoDecisionUpdate, 'expected_version' | 'reason'>>;
    result: DecisionDiagnostic }>;
}
export const fetchShadowHistory = (eventId: string, offset = 0) =>
  apiFetch<ShadowHistory>(`/api/auto-decisions/shadow?event_id=${encodeURIComponent(eventId)}&limit=20&offset=${offset}`);
