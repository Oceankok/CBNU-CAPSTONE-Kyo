import { parseRuleAudit } from '../autoRuleAudit';
import type { DecisionDiagnostic, RuleEvaluation } from '../api/autoDecisions';
import { closeLabel, decisionReason } from '../autoDecisionCopy';

function DecisionReasons({ reasons }: { reasons?: string[] }) {
  if (!reasons?.length) return null;
  return <ul>{[...new Set(reasons.map(decisionReason))].map(reason => <li key={reason}>{reason}</li>)}</ul>;
}

export function RuleObservations({ rule }: { rule: RuleEvaluation }) {
  return <div>
    <p>연속 관측 규칙 · {rule.eligible === true ? '자동 확정 조건 충족' : rule.eligible === false ? '자동 확정 조건 미충족' : '판정 결과 확인 불가'}</p>
    <dl>
      <dt>최소 관측 시간 / 실제</dt><dd>{rule.criteria?.minimum_observed_seconds ?? '미설정'}초 / {rule.observed_seconds ?? '미확인'}초</dd>
      <dt>최소 프레임 수 / 실제</dt><dd>{rule.criteria?.minimum_frame_samples ?? '미설정'} / {rule.frame_sample_count ?? '미확인'}</dd>
      <dt>관측 공백</dt><dd>{rule.gap_count ?? '미확인'}회 · {rule.gap_seconds ?? '미확인'}초</dd>
      {rule.allowed_rewear_gap && <><dt>안전모 재착용 예외</dt><dd>공백 전 미착용 확인 {rule.allowed_rewear_gap.samples_before}프레임 · 공백 시간 제외</dd></>}
      <dt>종료 사유</dt><dd>{closeLabel(rule.close_reason)}</dd>
    </dl>
    <DecisionReasons reasons={rule.blocking_reasons} />
  </div>;
}
export function DecisionExplanation({ item }: { item: DecisionDiagnostic }) {
  return <article><strong>{item.event_id}</strong>
    {item.rule_evaluation ? <RuleObservations rule={{ ...item.rule_evaluation, blocking_reasons: item.rule_evaluation.blocking_reasons ?? item.blocking_reasons }} /> : <>
      <p>모델 점수 확인 · {item.threshold_candidate ? '점수 기준 충족' : '점수 기준 미충족'} · 관측 점수 {item.observed_confidence ?? '없음'}</p>
      <DecisionReasons reasons={item.blocking_reasons} />
    </>}
    <details><summary>판정 세부 기록</summary><pre>{JSON.stringify(item, null, 2)}</pre></details>
  </article>;
}
export default function RuleReviewComment({ text, systemReview = false }: { text: string; systemReview?: boolean }) {
  const audit = systemReview ? parseRuleAudit(text) : null;
  if (!audit) return <span style={{ whiteSpace: 'pre-wrap' }}>{text}</span>;
  return <section aria-label="시스템 규칙 판단 근거"><h3>시스템의 연속 관측 규칙 판단</h3>
    <p>정책 버전 {audit.version} · 모델 {audit.model} · 정책 설정자 {audit.configuredBy}</p>
    <p>관리자는 영상을 확인하고 자동 확정 결과를 정정할 수 있습니다.</p>
    <RuleObservations rule={audit.rule} />
    <details><summary>자동 확정 세부 기록</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{text}</pre></details>
  </section>;
}
