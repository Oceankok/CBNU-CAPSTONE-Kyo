import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../api/client';
import { fetchAutoDecisionSettings, saveAutoDecisionSettings, previewAutoDecisions, fetchAutoDecisionHistory, fetchShadowHistory,
  type AutoDecisionHistory, type AutoDecisionSettings, type DecisionPreview, type ShadowHistory } from '../api/autoDecisions';
import { settingsSaveBlocker, settingsUpdate, settingsPreview } from '../autoDecisionSettings';
import { DecisionExplanation } from '../components/RuleDecisionExplanation';
import { formatSeoul } from '../time';
import { decisionReason, methodLabel, modeLabel } from '../autoDecisionCopy';
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
    }).catch(() => {
      if (request === identity.current) setMessage('설정을 불러오지 못했습니다. 다시 시도해 주세요.');
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
        setMessage('설정을 저장했습니다.');
      } catch (error) {
        if (!current()) return;
        if (error instanceof ApiError && error.status === 409 && error.message === 'auto_decision_settings_changed') {
          setConflict(true); setPreview(null);
          setMessage('다른 관리자가 설정을 변경했습니다. 최신 설정을 확인하고 현재 입력을 유지할지 선택하세요.');
          try { const data = await fetchAutoDecisionSettings(); if (current()) setLatest(data); }
          catch { if (current()) setMessage('최신 설정을 불러오지 못했습니다. 다시 시도해 주세요.'); }
        } else setMessage('설정을 저장하지 못했습니다. 입력한 내용은 그대로입니다.');
      }
    });
  };
  const diagnose = () => {
    if (!draft || settingsSaveBlocker(draft, false, 'preview')) return;
    void run(async current => {
      setPreview(null);
      const result = await previewAutoDecisions(settingsPreview(draft, values(ids)));
      if (current()) { setPreview(result); setMessage('현재 입력으로 확인했습니다. 사건 상태와 저장된 설정은 그대로입니다.'); }
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
    <header className={styles.header}>
      <div><h1>자동 확정</h1><p>보호구 미착용 사건의 자동 확정 기준과 시험 결과를 관리합니다.</p></div>
      <span className={styles.status} role="status">{saved ? modeLabel(saved.mode, saved.automatic_confirmation_enabled) : '설정 확인 중'}</span>
    </header>
    {message && <p className={styles.notice} role="status">{message}</p>}
    {!draft ? <button disabled={initialLoading || busy} onClick={() => { setMessage(''); setInitialLoading(true); setInitialAttempt(value => value + 1); }}>설정 다시 불러오기</button> : <>
      <section aria-label="자동 확정 준비 상태"><h2>자동 확정 준비 상태 · v{saved?.version}</h2>
        <p>저장된 설정 기준입니다.</p>
        {saved?.activation_blockers?.length ? <ul>{[...new Set(saved.activation_blockers.map(decisionReason))].map(value => <li key={value}>{value}</li>)}</ul> : <p>추가로 확인할 항목이 없습니다.</p>}
      </section>
      <fieldset disabled={busy} className={styles.policy}><legend>자동 확정 설정 · v{draft.version}</legend>
        <label>운영 모드<select value={draft.mode} onChange={e => change({ mode: e.target.value as AutoDecisionSettings['mode'] })}>
          <option value="off">꺼짐</option><option value="shadow">시험 모드 · 결과만 기록</option><option value="enforce" disabled>자동 확정 중 · 조회 전용</option>
        </select></label>
        <label>판단 방법<select value={draft.decision_method} onChange={e => change({ decision_method: e.target.value as AutoDecisionSettings['decision_method'], rule_criteria: null })}>
          <option value="validated_model">검증된 모델 점수</option><option value="continuous_rules_v1">연속 관측 규칙</option>
        </select></label>
        {draft.decision_method === 'continuous_rules_v1' ? <>
          <label>최소 미착용 관측 시간 (초)<input type="number" min="3" max="3600" step="any" value={Number.isFinite(draft.rule_criteria?.minimum_observed_seconds) ? draft.rule_criteria!.minimum_observed_seconds : ''} onChange={e => change({ rule_criteria: { ...draft.rule_criteria, minimum_frame_samples: draft.rule_criteria?.minimum_frame_samples ?? NaN, minimum_observed_seconds: e.target.value === '' ? NaN : Number(e.target.value) } })} /></label>
          <label>최소 관측 프레임 수<input type="number" min="2" max="100000" step="1" value={Number.isFinite(draft.rule_criteria?.minimum_frame_samples) ? draft.rule_criteria!.minimum_frame_samples : ''} onChange={e => change({ rule_criteria: { ...draft.rule_criteria, minimum_observed_seconds: draft.rule_criteria?.minimum_observed_seconds ?? NaN, minimum_frame_samples: e.target.value === '' ? NaN : Number(e.target.value) } })} /></label>
          <label className={styles.checkbox}><input type="checkbox" aria-describedby="rewear-gap-help" checked={draft.rule_criteria?.allow_terminal_helmet_rewear_gap ?? false} disabled={!draft.rule_criteria} onChange={e => { if (draft.rule_criteria) change({ rule_criteria: { ...draft.rule_criteria, allow_terminal_helmet_rewear_gap: e.target.checked } }); }} />안전모 재착용 직전의 짧은 탐지 공백 허용</label>
          <p id="rewear-gap-help" className={styles.help}>미착용 관측 기준을 충족한 다음 0.3초 이하 공백이 한 번 발생하고, 같은 사람의 재착용이 1초 안에 확인되면 적용됩니다.</p>
        </> : <label>판정에 사용할 최소 점수 (0~1)<input type="number" min="0" max="1" step="0.01" value={draft.threshold} onChange={e => change({ threshold: Number(e.target.value) })} /></label>}
        {(['model_versions', 'ppe_types', 'camera_ids', 'zone_names'] as const).map((field, i) => <label key={field}>{['모델 버전', '보호구 유형', '카메라 ID', '구역 이름'][i]} (한 줄에 하나)
          <textarea value={textFields[field] ?? draft[field].join('\n')} onChange={e => { setTextFields(old => ({ ...old, [field]: e.target.value })); change({ [field]: values(e.target.value) }); }} />
        </label>)}
        <p>모델, 보호구, 카메라를 모두 지정하세요. 구역을 비워 두면 선택한 카메라의 모든 구역에 적용합니다. 연속 관측 규칙은 안전모(helmet)와 조끼(vest)를 지원합니다.</p>
        <label>검증 기록 ID<input value={draft.validation_reference ?? ''} onChange={e => change({ validation_reference: e.target.value || null })} /></label>
        <p className={styles.help}>승인된 검증 기록의 ID를 입력하세요. 이 화면에서 꺼짐과 시험 모드를 저장할 수 있습니다.</p>
        <label>변경 사유<textarea value={reason} onChange={e => setReason(e.target.value)} /></label>
        {blocker && <p>{blocker}</p>}
        <button className={styles.primary} disabled={blocker !== null} onClick={save}>설정 저장</button>
      </fieldset>
      {conflict && <section><h2>다른 관리자가 설정을 변경했습니다</h2><button disabled={busy} onClick={() => void run(async current => { const data = await fetchAutoDecisionSettings(); if (current()) setLatest(data); })}>최신 설정 불러오기</button></section>}
      {latest && <section aria-label="최신 설정"><h2>최신 설정 · v{latest.version}</h2><p>{modeLabel(latest.mode, latest.automatic_confirmation_enabled)} · {methodLabel(latest.decision_method)}</p><details><summary>설정 세부 정보</summary><pre>{JSON.stringify(latest, null, 2)}</pre></details>
        <button disabled={busy} onClick={() => { setSaved(latest); setDraft(latest); setTextFields({}); setLatest(null); setConflict(false); setReason(''); setPreview(null); }}>입력을 버리고 최신 설정 사용</button>
        <button disabled={busy} onClick={() => { setSaved(latest); setDraft({ ...draft, version: latest.version }); setLatest(null); setConflict(false); setPreview(null); }}>현재 입력 유지</button>
        <p>변경 내용을 확인한 뒤 설정을 저장하세요.</p>
      </section>}
      <section aria-label="판정 미리보기"><h2>판정 미리보기</h2><p>현재 입력으로 과거 사건이 자동 확정 조건을 충족하는지 확인할 수 있습니다.</p>
        <label>사건 ID (최대 20개, 한 줄에 하나)<textarea disabled={busy} value={ids} onChange={e => { setIds(e.target.value); setPreview(null); }} /></label>
        <button disabled={busy || !!settingsSaveBlocker(draft, false, 'preview') || values(ids).length < 1 || values(ids).length > 20} onClick={diagnose}>판정 미리보기 실행</button>
        {preview?.items.map(item => <DecisionExplanation key={item.event_id} item={item} />)}
      </section>
    </>}
    <section aria-label="시험 모드 기록"><h2>시험 모드 기록</h2><p>당시 설정으로 기록한 시험 결과를 사건별로 확인할 수 있습니다.</p>
      <label>조회할 사건 ID<input disabled={busy} value={shadowId} onChange={e => { setShadowId(e.target.value); setShadow(null); }} /></label>
      <button disabled={initialLoading || busy || !shadowId.trim()} onClick={() => loadShadow()}>시험 기록 조회</button>
      {shadow && <>{shadow.items.length === 0 && <p>이 사건의 시험 기록이 없습니다.</p>}{shadow.items.map(item => <article key={item.run_id}>
        <p>정책 버전 {item.result.settings_version ?? '기록 없음'} · 방법 {methodLabel(item.policy.decision_method)} · {formatSeoul(item.created_at)} · 설정/요청자 {item.actor_id}</p>
        <details><summary>당시 정책</summary><pre>{JSON.stringify(item.policy, null, 2)}</pre></details><DecisionExplanation item={item.result} />
      </article>)}<button disabled={busy || shadow.offset === 0} onClick={() => loadShadow(Math.max(0, shadow.offset - 20))}>이전 기록</button><button disabled={busy || shadow.items.length < shadow.limit} onClick={() => loadShadow(shadow.offset + shadow.limit)}>다음 기록</button></>}
    </section>
    <section><h2>설정 변경 기록</h2><button disabled={initialLoading || busy} onClick={() => loadHistory()}>변경 기록 조회</button>
      {history && <><ol>{history.items.map(item => <li key={item.version}>버전 {item.version} · {formatSeoul(item.changed_at)} · 변경자 {item.changed_by}<p>{item.reason}</p><details><summary>당시 설정</summary><pre>{JSON.stringify(item.policy, null, 2)}</pre></details></li>)}</ol>
        <button disabled={busy || history.offset === 0} onClick={() => loadHistory(Math.max(0, history.offset - 20))}>이전 설정 이력</button><button disabled={busy || history.items.length < history.limit} onClick={() => loadHistory(history.offset + history.limit)}>다음 설정 이력</button></>}
    </section>
  </main>;
}
