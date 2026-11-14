import { useEffect, useState } from 'react';
import { ApiError } from '../api/client';
import {
  fetchAutoDecisionSettings, saveAutoDecisionSettings, previewAutoDecisions, fetchAutoDecisionHistory,
  type AutoDecisionHistory,
  type AutoDecisionSettings, type DecisionPreview,
} from '../api/autoDecisions';
import { maySelectEnforce, settingsSaveBlocker, settingsUpdate, settingsPreview } from '../autoDecisionSettings';
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

  useEffect(() => {
    let stale = false;
    fetchAutoDecisionSettings().then(data => {
      if (!stale) { setSaved(data); setDraft(data); setTextFields({}); }
    }).catch((error: Error) => { if (!stale) setMessage(error.message); });
    return () => { stale = true; };
  }, []);

  const change = (update: Partial<AutoDecisionSettings>) => {
    setDraft(old => old ? { ...old, ...update } : old);
    setPreview(null);
  };
  const save = async () => {
    if (!draft || busy || conflict) return;
    setBusy(true); setMessage('');
    try {
      const data = await saveAutoDecisionSettings(settingsUpdate(draft, conflict, reason));
      setHistory(null);
      setSaved(data); setDraft(data); setTextFields({}); setReason(''); setPreview(null);
      setMessage('설정을 저장했습니다. 저장 자체가 정확도 검증이나 자동확정 활성화를 뜻하지 않습니다.');
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setConflict(true);
        setMessage(`충돌 또는 활성화 조건 미충족으로 저장되지 않았습니다. 입력은 보존했습니다. 최신 설정을 확인해 주세요. 상세: ${error.message}`);
      } else setMessage(error instanceof Error ? error.message : '설정 저장 실패');
    } finally { setBusy(false); }
  };
  const reload = async () => {
    setBusy(true);
    try { setLatest(await fetchAutoDecisionSettings()); setMessage('최신 설정을 아래에 표시했습니다. 현재 입력은 아직 바꾸지 않았습니다.'); }
    catch (error) { setMessage(`최신 설정 조회 실패: ${error instanceof Error ? error.message : '알 수 없는 오류'}`); }
    finally { setBusy(false); }
  };
  const loadHistory = async (offset = 0) => {
    setBusy(true);
    try { setHistory(await fetchAutoDecisionHistory(offset)); }
    catch (error) { setMessage(`감사 이력 조회 실패: ${error instanceof Error ? error.message : '알 수 없는 오류'}`); }
    finally { setBusy(false); }
  };
  const diagnose = async () => {
    if (!draft) return;
    setBusy(true); setPreview(null);
    try {
      setPreview(await previewAutoDecisions(settingsPreview(draft, values(ids))));
      setMessage('미저장 입력의 카메라·과거 구역·모델·PPE 범위를 적용한 읽기 전용 진단입니다. 이벤트 판정은 변경하지 않았습니다.');
    } catch (error) { setMessage(error instanceof Error ? error.message : '진단 실패'); }
    finally { setBusy(false); }
  };

  return <main className={styles.page}>
    <h1>자동확정 설정 · 진단</h1>
    <p>사람 탐지 점수는 보호구 위반 확률이 아닙니다. 검토 전용·판단 불가·근거 부족 이벤트는 검토 대기로 남습니다.</p>
    <p role="status">서버 자동확정: {saved ? (saved.automatic_confirmation_enabled === true ? '활성' : saved.automatic_confirmation_enabled === false ? '비활성' : '확인 불가') : '확인 중'}</p>
    {message && <p role="status">{message}</p>}
    {!draft ? <button onClick={reload} disabled={busy}>다시 조회</button> : <>
      {(!Array.isArray(draft.activation_blockers) || draft.activation_blockers.length > 0) && <section><h2>활성화 차단 사유</h2><ul>{(Array.isArray(draft.activation_blockers) ? draft.activation_blockers : ['서버 준비 상태 응답 누락']).map(v => <li key={v}>{v}</li>)}</ul></section>}
      <fieldset disabled={busy}>
        <legend>정책 입력 (버전 {draft.version})</legend>
        <label>모드<select value={draft.mode} onChange={e => change({mode: e.target.value as AutoDecisionSettings['mode']})}>
          <option value="off">꺼짐</option><option value="shadow">그림자 진단 (확정하지 않음)</option>
          <option value="enforce" disabled={!maySelectEnforce(draft)}>자동확정 (검증된 서버 경로 필요)</option>
        </select></label>
        <label>실험 임계값 (0~1)<input type="number" min="0" max="1" step="0.01" value={draft.threshold} onChange={e => change({threshold: Number(e.target.value)})} /></label>
        <p>임계값을 저장하거나 낮춘 것만으로 검증되지 않은 후보가 확정되지 않습니다.</p>
        {(['model_versions', 'ppe_types', 'camera_ids', 'zone_names'] as const).map((field, i) => <label key={field}>
          {['모델 버전', 'PPE 유형', '카메라 ID', '구역 이름'][i]} (한 줄에 하나)
          <textarea value={textFields[field] ?? draft[field].join('\n')} onChange={e => { setTextFields(old => ({...old, [field]: e.target.value})); change({[field]: values(e.target.value)}); }} />
        </label>)}
        <p>모델·PPE·카메라 목록이 비면 적용 대상이 없습니다. 구역 목록이 비면 선택한 카메라에 추가 구역 제한을 걸지 않습니다.</p>
        <label>검증 참조<input value={draft.validation_reference ?? ''} onChange={e => change({validation_reference: e.target.value || null})} /></label>
        <p>검증 참조 문자열 입력은 검증 승인 자체가 아닙니다. 실제 증거와 서버 검증기가 필요합니다.</p>
        <label>변경 사유<textarea value={reason} onChange={e => setReason(e.target.value)} /></label>
        <button onClick={save} disabled={settingsSaveBlocker(draft, conflict, reason) !== null}>설정 저장</button>
      </fieldset>
      {conflict && <section><h2>저장 충돌 확인</h2><button disabled={busy} onClick={reload}>최신 설정 조회</button></section>}
      <section><h2>이벤트 진단</h2><p>목록·상세 상태를 변경하지 않는 preview입니다. 현재 입력을 사용하며 확정 결과나 검증된 정확도가 아닙니다.</p>
        <label>이벤트 ID (1~20개, 한 줄에 하나)<textarea disabled={busy} value={ids} onChange={e => { setIds(e.target.value); setPreview(null); }} /></label>
        <button disabled={busy || values(ids).length < 1 || values(ids).length > 20} onClick={diagnose}>읽기 전용 진단</button>
        {preview && <ul>{preview.items.map(item => <li key={item.event_id}>{item.event_id}: {item.threshold_candidate ? '실험 임계값 후보 (확정 아님)' : '임계값 후보 아님'} · 점수 {item.observed_confidence ?? '없음'}<ul>{item.blocking_reasons.map(v => <li key={v}>{v}</li>)}</ul></li>)}</ul>}
      </section>
    </>}
    <section><h2>설정 변경 감사 이력</h2><button disabled={busy} onClick={() => loadHistory()}>최근 20건 조회</button>
      {history && <><ol>{history.items.map(item => <li key={item.version}>버전 {item.version} · {item.changed_at} · 변경자 {item.changed_by} · 사유 {item.reason}<pre>{JSON.stringify(item.policy, null, 2)}</pre></li>)}</ol>
        <button disabled={busy || history.offset === 0} onClick={() => loadHistory(Math.max(0, history.offset - 20))}>이전 이력</button>
        <button disabled={busy || history.items.length < history.limit} onClick={() => loadHistory(history.offset + history.limit)}>다음 이력</button></>}
    </section>
    {latest && <section><h2>최신 서버 설정 (현재 입력과 별도)</h2><pre>{JSON.stringify(latest, null, 2)}</pre>
      <button disabled={busy} onClick={() => { setDraft(latest); setSaved(latest); setTextFields({}); setLatest(null); setConflict(false); setReason(''); setPreview(null); setMessage('기존 입력을 버리고 최신 설정으로 다시 시작했습니다.'); }}>기존 입력을 버리고 최신 설정 사용</button>
    </section>}
  </main>;
}
