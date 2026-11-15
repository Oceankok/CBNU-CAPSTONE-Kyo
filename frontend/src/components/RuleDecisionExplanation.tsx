import { parseRuleAudit } from '../autoRuleAudit';
import type { DecisionDiagnostic, RuleEvaluation } from '../api/autoDecisions';

export function RuleObservations({ rule }: { rule: RuleEvaluation }) {
  return <div>
    <p>연속 관측 규칙 · {rule.eligible ? '규칙 조건 충족' : '규칙 조건 미충족'} · 확률 점수 없음</p>
    <dl>
      <dt>최소 관측 시간 / 실제</dt><dd>{rule.criteria?.minimum_observed_seconds ?? '미설정'}초 / {rule.observed_seconds ?? '미확인'}초</dd>
      <dt>최소 샘플 / 실제</dt><dd>{rule.criteria?.minimum_frame_samples ?? '미설정'} / {rule.frame_sample_count ?? '미확인'}</dd>
      <dt>관측 공백</dt><dd>{rule.gap_count ?? '미확인'}회 · {rule.gap_seconds ?? '미확인'}초</dd>
      <dt>종료 사유</dt><dd>{rule.close_reason ?? '미확인'}</dd>
    </dl>
    <p>제외 사유: {rule.blocking_reasons.length ? rule.blocking_reasons.join(', ') : '기록된 제외 사유 없음'}</p>
    <p>한계: {rule.limitations.join(', ')}</p>
  </div>;
}
export function DecisionExplanation({ item }: { item: DecisionDiagnostic }) {
  return <article><strong>{item.event_id}</strong>
    {item.rule_evaluation ? <RuleObservations rule={item.rule_evaluation} /> : <>
      <p>점수 경로 진단 · {item.threshold_candidate ? '임계값 후보 (확정 아님)' : '임계값 후보 아님'} · 관측 점수 {item.observed_confidence ?? '없음'}</p>
      <p>제외 사유: {item.blocking_reasons.join(', ') || '기록 없음'}</p>
    </>}
    <p>근거 지문: {item.evidence_fingerprint ?? '기록 없음'}</p>
  </article>;
}
export default function RuleReviewComment({ text, systemReview = false }: { text: string; systemReview?: boolean }) {
  const audit = systemReview ? parseRuleAudit(text) : null;
  if (!audit) return <span style={{ whiteSpace: 'pre-wrap' }}>{text}</span>;
  return <section aria-label="시스템 규칙 판단 근거"><h3>시스템의 연속 관측 규칙 판단</h3>
    <p>정책 버전 {audit.version} · 모델 {audit.model} · 정책 설정자 {audit.configuredBy}</p>
    <p>원래 탐지의 검토 전용 표시는 점수 출처입니다. 최종 검토 상태와 구분하며 관리자 재검토가 가능합니다.</p>
    <RuleObservations rule={audit.rule} /><p>근거 지문: {audit.fingerprint}</p>
    <details><summary>원본 감사 기록</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{text}</pre></details>
  </section>;
}
