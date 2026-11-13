import { apiFetch } from './client';

export interface AutoDecisionSettings {
  version: number;
  mode: 'off' | 'shadow' | 'enforce';
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
  items: Array<{event_id: string; observed_confidence: number | null;
    threshold_candidate: boolean; blocking_reasons: string[]}>;
}
export const fetchAutoDecisionSettings = () =>
  apiFetch<AutoDecisionSettings>('/api/auto-decisions/settings');
export const saveAutoDecisionSettings = (body: AutoDecisionUpdate) =>
  apiFetch<AutoDecisionSettings>('/api/auto-decisions/settings', {
    method: 'PUT', body: JSON.stringify(body),
  });
export const previewAutoDecisions = (body: {
  event_ids: string[]; threshold: number; model_versions: string[];
  ppe_types: string[]; camera_ids: string[]; zone_names: string[];
  mode: 'off' | 'shadow' | 'enforce'; validation_reference: string | null;
}) => apiFetch<DecisionPreview>('/api/auto-decisions/settings/preview', {
  method: 'POST', body: JSON.stringify(body),
});

export interface AutoDecisionHistory {
  items: Array<{ version: number; policy: Omit<AutoDecisionUpdate, 'expected_version' | 'reason'>;
    changed_by: string; changed_at: string; reason: string }>;
  limit: number; offset: number;
}
export const fetchAutoDecisionHistory = (offset = 0) =>
  apiFetch<AutoDecisionHistory>(`/api/auto-decisions/settings/history?limit=20&offset=${offset}`);
