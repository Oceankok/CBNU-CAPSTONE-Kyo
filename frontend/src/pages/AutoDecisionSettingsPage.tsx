import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../api/client';
import { fetchAutoDecisionSettings, saveAutoDecisionSettings, previewAutoDecisions, fetchAutoDecisionHistory, fetchShadowHistory,
  type AutoDecisionHistory, type AutoDecisionSettings, type DecisionPreview, type ShadowHistory } from '../api/autoDecisions';
import { settingsSaveBlocker, settingsUpdate, settingsPreview } from '../autoDecisionSettings';
import { DecisionExplanation } from '../components/RuleDecisionExplanation';
import styles from './AutoDecisionSettingsPage.module.css';

const values = (text: string) => [...new Set(text.split('\n').map(v => v.trim()).filter(Boolean))];
export default function AutoDecisionSettingsPage() {
  const [draft, setDraft] = useState<AutoDecisionSettings | null>(null);
  const [saved, setSaved] = useState<AutoDecisionSettings | null>(null);
  const [latest, setLatest] = useState<AutoDecisionSettings | null>(null);
  const [conflict, setConflict] = useState(false);
  const [reason, setReason] = useState('');
  const [textFields, setTextFields] = useState<Record<string, string>>({});
  const [ids, setIds] = useState('');
  const [preview, setPreview] = useState<DecisionPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [history, setHistory] = useState<AutoDecisionHistory | null>(null);
  const [shadowId, setShadowId] = useState('');
  const [shadow, setShadow] = useState<ShadowHistory | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [initialAttempt, setInitialAttempt] = useState(0);
  const operation = useRef(false);
  const epoch = useRef(0);
  useEffect(() => {
    const identity = epoch;
    const request = ++identity.current;
    fetchAutoDecisionSettings().then(data => {
      if (request === identity.current) { setSaved(data); setDraft(data); setTextFields({}); }
    }).catch((error: unknown) => {
      if (request === identity.current) setMessage(`설정 조회 실패: ${error instanceof Error ? error.message : String(error)}`);
    }).finally(() => { if (request === identity.current) setInitialLoading(false); });
    return () => { ++identity.current; };
  }, [initialAttempt]);
  const run = async (action: (current: () => boolean) => Promise<void>) => {
    if (operation.current || initialLoading) return;
    operation.current = true; setBusy(true);
    const identity = epoch.current;
    const current = () => identity === epoch.current;
    try { await action(current); }
    catch (error) { if (current()) setMessage(error instanceof Error ? error.message : String(error)); }
    finally { operation.current = false; if (current()) setBusy(false); }
  };
  const change = (update: Partial<AutoDecisionSettings>) => {
    if (operation.current) return;
    setDraft(old => old ? { ...old, ...update } : old); setPreview(null);
  };
  const save = () => {
    if (!draft || settingsSaveBlocker(draft, conflict, reason)) return;
    void run(async current => {
      setMessage('');
      try {
        const data = await saveAutoDecisionSettings(settingsUpdate(draft, conflict, reason));
        if (!current()) return;
        setSaved(data); setDraft(data); setTextFields({}); setReason(''); setPreview(null); setHistory(null); setLatest(null);
        setMessage('설정을 저장했습니다. 검증 승인이나 자동확정 활성화가 아닙니다.');
      } catch (error) {
        if (!current()) return;
        if (error instanceof ApiError && error.status === 409 && error.message === 'auto_decision_settings_changed') {
          setConflict(true); setPreview(null);
          setMessage('다른 관리자가 설정을 변경했습니다. 입력을 보존했습니다. 최신 설정을 확인하고 명시적으로 선택하세요.');
          try { const data = await fetchAutoDecisionSettings(); if (current()) setLatest(data); }
          catch { if (current()) setMessage('최신 설정 조회에 실패했습니다. 입력을 보존했습니다. 다시 조회하세요.'); }
        } else setMessage(`저장하지 못했습니다. 입력은 보존됩니다. ${error instanceof Error ? error.message : String(error)}`);
      }
    });
  };
  const diagnose = () => {
    if (!draft || settingsSaveBlocker(draft, false, 'preview')) return;
    void run(async current => {
      setPreview(null);
      const result = await previewAutoDecisions(settingsPreview(draft, values(ids)));
      if (current()) { setPreview(result); setMessage('현재 미저장 입력의 읽기 전용 진단입니다. 판정과 shadow 이력을 저장하지 않았습니다.'); }
    });
  };
  const loadHistory = (offset = 0) => void run(async current => {
    const data = await fetchAutoDecisionHistory(offset); if (current()) setHistory(data);
  });
  const loadShadow = (offset = 0) => void run(async current => {
    const data = await fetchShadowHistory(shadowId.trim(), offset); if (current()) setShadow(data);
  });
  const blocker = draft ? settingsSaveBlocker(draft, conflict, reason) : null;
  return <main className={styles.page}>
    <h1>자동확정 설정 · 진단</h1>
    <p>사람 탐지 점수는 보호구 위반 확률이 아닙니다. 연속 관측 규칙은 관측 지속성과 근거 완전성을 검사합니다. 원래 검토 전용 표시는 점수의 출처이며 최종 검토 상태와 구분합니다.</p>
    <p role="status">저장된 서버 모드: {saved?.mode ?? '확인 중'} · 자동확정: {saved ? saved.automatic_confirmation_enabled ? '활성' : '비활성' : '확인 불가'}</p>
    <p>이번 관리 화면은 OFF·shadow 저장만 지원합니다. 실제 활성화는 별도 검증과 결정이 필요합니다.</p>
    {message && <p role="status">{message}</p>}
    {!draft ? <button disabled={initialLoading || busy} onClick={() => { setInitialLoading(true); setInitialAttempt(value => value + 1); }}>설정 다시 조회</button> : <>
      <section aria-label="저장된 설정의 활성화 차단 사유"><h2>저장된 설정의 활성화 차단 사유 · v{saved?.version}</h2>
        <p>저장 당시 정책의 서버 응답입니다. 아래 미저장 입력의 준비 상태나 활성화 승인이 아닙니다.</p>
        <ul>{(saved?.activation_blockers?.length ? saved.activation_blockers : ['서버 응답에 기록된 차단 사유 없음 (활성화 승인 아님)']).map(value => <li key={value}>{value}</li>)}</ul>
      </section>
      <fieldset disabled={busy}><legend>정책 입력 · 기준 버전 {draft.version}</legend>
        <label>모드<select value={draft.mode} onChange={e => change({ mode: e.target.value as AutoDecisionSettings['mode'] })}>
          <option value="off">OFF · 꺼짐</option><option value="shadow">shadow · 진단만 기록</option><option value="enforce" disabled>enforce · 현재 상태 조회 전용</option>
        </select></label>
        <label>판단 방법<select value={draft.decision_method} onChange={e => change({ decision_method: e.target.value as AutoDecisionSettings['decision_method'], rule_criteria: null })}>
          <option value="validated_model">검증 모델 점수 경로</option><option value="continuous_rules_v1">연속 관측 규칙 v1</option>
        </select></label>
        {draft.decision_method === 'continuous_rules_v1' ? <>
          <label>최소 실제 관측 시간 (3~3600초)<input type="number" min="3" max="3600" step="any" value={Number.isFinite(draft.rule_criteria?.minimum_observed_seconds) ? draft.rule_criteria!.minimum_observed_seconds : ''} onChange={e => change({ rule_criteria: { minimum_frame_samples: draft.rule_criteria?.minimum_frame_samples ?? NaN, minimum_observed_seconds: e.target.value === '' ? NaN : Number(e.target.value) } })} /></label>
          <label>최소 프레임 샘플 (2~100000 정수)<input type="number" min="2" max="100000" step="1" value={Number.isFinite(draft.rule_criteria?.minimum_frame_samples) ? draft.rule_criteria!.minimum_frame_samples : ''} onChange={e => change({ rule_criteria: { minimum_observed_seconds: draft.rule_criteria?.minimum_observed_seconds ?? NaN, minimum_frame_samples: e.target.value === '' ? NaN : Number(e.target.value) } })} /></label>
          <p>이 값은 관리자가 명시해야 합니다. 기본 추천값이나 확률 임계값이 아닙니다. 전체 사건·영상 근거와 공백·종료 조건을 서버가 함께 검사합니다.</p>
        </> : <label>실험 점수 임계값 (0~1)<input type="number" min="0" max="1" step="0.01" value={draft.threshold} onChange={e => change({ threshold: Number(e.target.value) })} /></label>}
        {(['model_versions', 'ppe_types', 'camera_ids', 'zone_names'] as const).map((field, i) => <label key={field}>{['모델 버전', 'PPE 유형', '카메라 ID', '구역 이름'][i]} (한 줄에 하나)
          <textarea value={textFields[field] ?? draft[field].join('\n')} onChange={e => { setTextFields(old => ({ ...old, [field]: e.target.value })); change({ [field]: values(e.target.value) }); }} />
        </label>)}
        <p>모델·PPE·카메라 목록이 비면 대상이 없습니다. 구역 목록이 비면 추가 구역 제한이 없습니다. 규칙 경로는 helmet·vest만 지원합니다.</p>
        <label>검증 참조<input value={draft.validation_reference ?? ''} onChange={e => change({ validation_reference: e.target.value || null })} /></label>
        <p>참조 문자열 입력은 검증 승인이 아닙니다.</p>
        <label>변경 사유<textarea value={reason} onChange={e => setReason(e.target.value)} /></label>
        {blocker && <p>{blocker}</p>}
        <button disabled={blocker !== null} onClick={save}>OFF·shadow 설정 저장</button>
      </fieldset>
      {conflict && <section><h2>설정 버전 충돌</h2><button disabled={busy} onClick={() => void run(async current => { const data = await fetchAutoDecisionSettings(); if (current()) setLatest(data); })}>최신 설정 조회</button></section>}
      {latest && <section aria-label="최신 서버 설정"><h2>최신 서버 설정 · v{latest.version}</h2><pre>{JSON.stringify(latest, null, 2)}</pre>
        <button disabled={busy} onClick={() => { setSaved(latest); setDraft(latest); setTextFields({}); setLatest(null); setConflict(false); setReason(''); setPreview(null); }}>입력 취소·최신 설정으로 시작</button>
        <button disabled={busy} onClick={() => { setSaved(latest); setDraft({ ...draft, version: latest.version }); setLatest(null); setConflict(false); setPreview(null); }}>입력 유지·최신 버전 선택</button>
        <p>선택 자체는 저장하지 않습니다. 최신 정책과 비교한 뒤 별도 저장하세요.</p>
      </section>}
      <section aria-label="현재 입력 진단"><h2>현재 미저장 입력의 진단</h2><p>현재 입력 기준이며 판정·검증 승인·저장된 shadow 결과가 아닙니다.</p>
        <label>이벤트 ID (1~20개, 한 줄에 하나)<textarea disabled={busy} value={ids} onChange={e => { setIds(e.target.value); setPreview(null); }} /></label>
        <button disabled={busy || !!settingsSaveBlocker(draft, false, 'preview') || values(ids).length < 1 || values(ids).length > 20} onClick={diagnose}>현재 입력 읽기 전용 진단</button>
        {preview?.items.map(item => <DecisionExplanation key={item.event_id} item={item} />)}
      </section>
    </>}
    <section aria-label="저장된 shadow 이력"><h2>저장된 shadow 이력</h2><p>과거 정책·근거의 진단 기록입니다. 현재 입력이나 현재 사건 상태, 실제 확정 결과가 아닙니다.</p>
      <label>shadow 조회 이벤트 ID<input disabled={busy} value={shadowId} onChange={e => { setShadowId(e.target.value); setShadow(null); }} /></label>
      <button disabled={initialLoading || busy || !shadowId.trim()} onClick={() => loadShadow()}>저장된 shadow 조회</button>
      {shadow && <>{shadow.items.length === 0 && <p>저장된 shadow 기록이 없습니다.</p>}{shadow.items.map(item => <article key={item.run_id}>
        <p>정책 버전 {item.result.settings_version ?? '기록 없음 (이전/수동 진단)'} · 방법 {item.policy.decision_method ?? '이전 점수 진단'} · {item.created_at} · 설정/요청자 {item.actor_id}</p>
        <details><summary>당시 정책</summary><pre>{JSON.stringify(item.policy, null, 2)}</pre></details><DecisionExplanation item={item.result} />
      </article>)}<button disabled={busy || shadow.offset === 0} onClick={() => loadShadow(Math.max(0, shadow.offset - 20))}>이전 shadow 이력</button><button disabled={busy || shadow.items.length < shadow.limit} onClick={() => loadShadow(shadow.offset + shadow.limit)}>다음 shadow 이력</button></>}
    </section>
    <section><h2>설정 변경 감사 이력</h2><button disabled={initialLoading || busy} onClick={() => loadHistory()}>최근 설정 변경 20건 조회</button>
      {history && <><ol>{history.items.map(item => <li key={item.version}>버전 {item.version} · {item.changed_at} · 변경자 {item.changed_by} · 사유 {item.reason}<pre>{JSON.stringify(item.policy, null, 2)}</pre></li>)}</ol>
        <button disabled={busy || history.offset === 0} onClick={() => loadHistory(Math.max(0, history.offset - 20))}>이전 설정 이력</button><button disabled={busy || history.items.length < history.limit} onClick={() => loadHistory(history.offset + history.limit)}>다음 설정 이력</button></>}
    </section>
  </main>;
}
