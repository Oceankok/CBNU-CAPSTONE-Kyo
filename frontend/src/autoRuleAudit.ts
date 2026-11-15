import type { RuleEvaluation } from './api/autoDecisions';

const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(item => typeof item === 'string');
const numeric = (value: unknown) => value === null || (typeof value === 'number' && Number.isFinite(value));
export function parseRuleAudit(text: string): { version: number; model: string; fingerprint: string; configuredBy: string; rule: RuleEvaluation } | null {
  if (text.length > 100000) return null;
  try {
    const audit: unknown = JSON.parse(text);
    if (!record(audit) || audit.source !== 'automatic_continuous_rules_v1' || !Number.isInteger(audit.settings_version) || !record(audit.policy) || audit.policy.decision_method !== 'continuous_rules_v1' || typeof audit.model_version !== 'string' || typeof audit.evidence_fingerprint !== 'string' || typeof audit.policy_configured_by !== 'string') return null;
    const rule = audit.rule_evaluation;
    if (!record(rule) || rule.method !== 'continuous_rules_v1' || typeof rule.eligible !== 'boolean' || rule.score !== null || !strings(rule.blocking_reasons) || !strings(rule.limitations) || !['observed_seconds', 'frame_sample_count', 'gap_count', 'gap_seconds'].every(key => numeric(rule[key])) || !(rule.close_reason === null || typeof rule.close_reason === 'string')) return null;
    if (!(rule.criteria === null || (record(rule.criteria) && typeof rule.criteria.minimum_observed_seconds === 'number' && Number.isFinite(rule.criteria.minimum_observed_seconds) && Number.isInteger(rule.criteria.minimum_frame_samples)))) return null;
    return { version: audit.settings_version as number, model: audit.model_version, fingerprint: audit.evidence_fingerprint, configuredBy: audit.policy_configured_by, rule: rule as unknown as RuleEvaluation };
  } catch { return null; }
}
