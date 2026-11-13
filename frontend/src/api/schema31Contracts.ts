export interface MaterialScopeLike {
  recommendation_id: string;
  quarter: string;
  zone_name: string;
  ppe_type: string;
  education_topic: string;
}

export interface MaterialContentLike {
  title: string;
  body: string;
  checklist: string[];
}

export interface ZoneRuleLike {
  required_ppe: unknown[];
  rules: string[];
  risk_level?: number | null;
}

export function appendRepeatedZones(
  params: URLSearchParams,
  zones: string[],
): URLSearchParams {
  for (const zone of zones) params.append('zones', zone);
  return params;
}

export function materialGenerateBody(expectedScope: MaterialScopeLike) {
  return { expected_scope: expectedScope };
}

export function materialCreateBody(
  content: MaterialContentLike,
  expectedScope: MaterialScopeLike,
) {
  return { ...content, expected_scope: expectedScope };
}

export function relationFingerprintBody(expectedFingerprint: string) {
  return { expected_fingerprint: expectedFingerprint };
}

export function zoneSaveBody<T extends ZoneRuleLike>(
  rule: T,
): Pick<T, 'required_ppe' | 'rules'> & { risk_level?: T['risk_level'] } {
  return {
    required_ppe: rule.required_ppe,
    rules: rule.rules,
    ...(rule.risk_level != null ? { risk_level: rule.risk_level } : {}),
  };
}
