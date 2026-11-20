import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '../api/client';
import { changeTranslationPublication, createTranslation, fetchTranslationSource, listTranslations, listAutomaticTranslations, updateTranslation, type AutomaticTranslation, type EducationTranslation, type TranslationSource, type TranslationSourceKind } from '../api/educationTranslations';
import ConfirmButton from './ConfirmButton';
import styles from './EducationMaterialEditor.module.css';

interface Draft { language: string; title: string; body: string; checklist: string; editing: EducationTranslation | null; sourceVersion: number }
const AUTO_STATUS = { queued: '번역 준비 대기', dispatched: '번역 준비 중', ready: '번역 준비됨', failed: '번역 실패', unknown: '번역 결과 확인 불가', stale: '원본 변경으로 사용할 수 없음' };
export default function EducationTranslationEditor({ kind, sourceId, sourceVersion: propVersion, published: propPublished, disabled = false }: {
  kind: TranslationSourceKind; sourceId: string; sourceVersion: number; published: boolean; disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<EducationTranslation[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflictId, setConflictId] = useState<string | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const [automatic, setAutomatic] = useState<AutomaticTranslation[]>([]);
  const [automaticError, setAutomaticError] = useState<string | null>(null);
  const [latestSource, setLatestSource] = useState<TranslationSource | null>(null);
  const [sourceOverride, setSourceOverride] = useState<{ kind: string; id: string; propVersion: number; propPublished: boolean; source: TranslationSource } | null>(null);
  const override = sourceOverride?.kind === kind && sourceOverride.id === sourceId && sourceOverride.propVersion === propVersion && sourceOverride.propPublished === propPublished ? sourceOverride.source : null;
  const sourceVersion = override?.version ?? propVersion;
  const published = override ? override.status === 'published' : propPublished;
  const request = useRef(0);
  const identity = useRef(0);
  const mutationLock = useRef(false);
  const locked = busy || disabled;
  const stale = Boolean(draft && (draft.sourceVersion !== sourceVersion || !published));
  const latest = items.find(item => item.translation_id === conflictId);
  const load = useCallback(async () => {
    const epoch = ++request.current;
    const result = await listTranslations(kind, sourceId);
    if (epoch === request.current) setItems(result.items);
    return result;
  }, [kind, sourceId]);
  useEffect(() => {
    if (!open) return;
    const epoch = request;
    const identityEpoch = identity;
    let active = true;
    const reading = load();
    const listEpoch = epoch.current;
    void reading.catch((e: Error) => { if (active && listEpoch === epoch.current) setError(e.message); });
    if (published) listAutomaticTranslations(kind, sourceId, sourceVersion)
      .then(result => { if (active) { setAutomatic(result.items); setAutomaticError(null); } })
      .catch(() => { if (active) setAutomaticError('자동 번역 결과를 조회하지 못했습니다. 수동 작성은 계속할 수 있습니다.'); });
    return () => { active = false; ++epoch.current; ++identityEpoch.current; };
  }, [open, kind, sourceId, sourceVersion, published, load]);
  const refreshSource = async () => {
    const generation = identity.current;
    const result = await fetchTranslationSource(kind, sourceId);
    if (generation === identity.current) setLatestSource(result);
  };
  const mutate = async (action: () => Promise<EducationTranslation>, id?: string) => {
    if (locked || mutationLock.current) return;
    mutationLock.current = true;
    const generation = identity.current;
    setBusy(true); setError(null);
    try {
      const next = await action();
      if (generation !== identity.current) return;
      if (draft && (!id || draft.editing?.translation_id === id)) {
        setDraft({ ...draft, editing: next, sourceVersion: next.source_version });
      }
      setConflictId(null); setReviewed(true);
      await load();
    } catch (e) {
      if (generation !== identity.current) return;
      if (e instanceof ApiError && e.status === 409) {
        const sourceConflict = e.message === 'education_translation_source_changed' || e.message === 'education_translation_source_not_published';
        setConflictId(sourceConflict ? 'source' : id ?? 'source'); setReviewed(false);
        setError('번역 또는 한국어 원본이 변경되었습니다. 입력을 보존했습니다. 최신 내용을 확인하고 명시적으로 다시 선택하세요.');
        try { await load(); if (generation === identity.current && (sourceConflict || !id)) await refreshSource(); } catch { setError('최신 자료 조회에 실패했습니다. 입력을 보존했습니다. 다시 조회하세요.'); }
      } else setError(e instanceof Error ? e.message : String(e));
    } finally { mutationLock.current = false; setBusy(false); }
  };
  const edit = (item: EducationTranslation) => {
    setDraft({ editing: item, language: item.language, title: item.title, body: item.body, checklist: item.checklist.join('\n'), sourceVersion: item.source_version });
    setConflictId(null); setReviewed(true); setError(null);
  };
  return <section aria-label="수동 번역 관리" className={styles.wrap}>
    <button type="button" disabled={locked} onClick={() => setOpen(!open)}>수동 번역 {open ? '닫기' : '작성·게시'}</button>
    {open && <div className={styles.panel}>
      <p>한국어 원본 v{sourceVersion} · {published ? '게시됨' : '게시 안 됨'}. 수동 번역을 게시하면 해당 언어의 자동 번역보다 우선합니다.</p>
      {!published && <p role="status">한국어 원본을 먼저 게시하세요. 기존 입력은 복사하거나 보존할 수 있습니다.</p>}
      {error && <p role="alert">{error}</p>}
      {automaticError && <p role="status">{automaticError}</p>}
      {(stale || conflictId === 'source' || latestSource) && <div role="alert">
        <button type="button" disabled={locked} onClick={() => {
          setBusy(true);
          void refreshSource().catch((e: Error) => setError(`최신 한국어 원본 조회 실패: ${e.message}`)).finally(() => setBusy(false));
        }}>최신 한국어 원본 조회</button>
        {latestSource && <>
          <strong>최신 한국어 원본 v{latestSource.version} · {latestSource.status === 'published' ? '게시됨' : '게시 안 됨'}</strong>
          <p>{latestSource.title}</p><p>{latestSource.body}</p><ul>{latestSource.checklist.map((line, index) => <li key={index}>{line}</li>)}</ul>
          {latestSource.status === 'published' ? <>
            <p>변경된 원문과 번역을 다시 비교하세요. 선택하면 기존 번역을 덮어쓰지 않고 이 입력으로 새 번역 초안을 작성합니다.</p>
            <button type="button" disabled={locked} onClick={() => {
              setSourceOverride({ kind, id: sourceId, propVersion, propPublished, source: latestSource });
              if (draft) setDraft({ ...draft, editing: null, sourceVersion: latestSource.version });
              setLatestSource(null); setConflictId(null); setError(null); setReviewed(false);
            }}>입력 유지·최신 원본으로 새 번역 선택</button>
          </> : <p>한국어 원본이 게시되지 않아 번역을 저장·게시할 수 없습니다. 입력은 유지됩니다. 원본 게시 후 다시 조회하세요.</p>}
        </>}
      </div>}
      {automatic.filter(item => published && item.source_id === sourceId && item.source_kind === kind && item.source_version === sourceVersion).map(item => <div key={item.language}>
        <p>AI 자동 번역 · {item.language} · {AUTO_STATUS[item.status]}</p>
        {item.status === 'ready' && item.content && <>
          <details><summary>자동 번역 결과 보기</summary><h4>{item.content.title}</h4><p>{item.content.body}</p><ul>{item.content.checklist.map((line, i) => <li key={i}>{line}</li>)}</ul></details>
          <button type="button" disabled={locked || Boolean(draft)} onClick={() => {
            if (!item.content) return;
            setDraft({ editing: null, language: item.language, title: item.content.title, body: item.content.body, checklist: item.content.checklist.join('\n'), sourceVersion });
            setConflictId(null); setReviewed(false); setError(null);
          }}>자동 번역을 수동 초안으로 복사 ({item.language})</button>
        </>}
      </div>)}
      <button type="button" disabled={locked} onClick={() => { setBusy(true); void load().catch((e: Error) => setError(e.message)).finally(() => setBusy(false)); }}>번역 목록 다시 조회</button>
      {items.map(item => <article key={item.translation_id}>
        <strong>{item.language} · {item.title} · v{item.version}</strong>
        <p>{item.status === 'published' ? '게시됨' : item.status === 'draft' ? '초안' : '교체됨'} · {item.provider === 'manual' ? '수동 번역' : 'AI로 작성한 검토용 번역'} · 원본 v{item.source_version}{item.stale ? ' · 원본 변경/게시 취소로 사용할 수 없음' : ''}</p>
        <details><summary>번역 내용</summary><p>{item.body}</p><ul>{item.checklist.map((line, i) => <li key={i}>{line}</li>)}</ul></details>
        <button type="button" disabled={locked || Boolean(draft) || !published || item.source_version !== sourceVersion || item.stale || item.status !== 'draft'} onClick={() => edit(item)}>번역 수정</button>
        <ConfirmButton label={item.status === 'published' ? '번역 게시 취소' : '번역 게시'} confirmLabel="확인" question="이 수동 번역의 게시 상태를 변경하시겠습니까?" disabled={locked || Boolean(conflictId) || Boolean(draft) || (item.status !== 'published' && (!published || item.source_version !== sourceVersion || item.stale || item.status !== 'draft'))} onConfirm={() => void mutate(() => changeTranslationPublication(item.translation_id, item.version, item.status !== 'published'), item.translation_id)} />
      </article>)}
      {conflictId && <div role="alert">
        <strong>최신 번역 확인이 필요합니다</strong>
        {latest && <><p>{latest.title}</p><p>{latest.body}</p><ul>{latest.checklist.map((line, i) => <li key={i}>{line}</li>)}</ul>
          <p>최신 버전 {latest.version} · {latest.status === 'published' ? '게시됨' : latest.status === 'draft' ? '초안' : '교체됨'}{latest.stale ? ' · 원본 변경됨' : ''}</p></>}
        {draft?.editing && latest && <button type="button" disabled={locked || !published || latest.source_version !== sourceVersion || latest.stale || latest.status !== 'draft'} onClick={() => { setDraft({ ...draft, editing: latest }); setConflictId(null); setReviewed(false); }}>입력 유지·최신 번역 버전 선택</button>}
        {!draft && latest && <button type="button" disabled={locked} onClick={() => { setConflictId(null); setError(null); }}>최신 번역 확인 완료</button>}
        {draft && <p>원본이 변경된 경우 입력을 복사한 뒤 편집을 종료하고 최신 원본에서 새 번역을 작성하세요.</p>}
      </div>}
      {draft ? <div className={styles.form}>
        {stale && <p role="alert">원본이 변경되거나 게시 취소되었습니다. 이 입력으로 저장·게시할 수 없습니다.</p>}
        <label className={styles.field}>번역 언어<input disabled={locked || Boolean(draft.editing)} value={draft.language} placeholder="en, vi, zh-CN" onChange={e => setDraft({ ...draft, language: e.target.value })} /></label>
        <label className={styles.field}>번역 제목<input disabled={locked} maxLength={200} value={draft.title} onChange={e => { setDraft({ ...draft, title: e.target.value }); setReviewed(false); }} /></label>
        <label className={styles.field}>번역 본문<textarea disabled={locked} maxLength={12000} rows={5} value={draft.body} onChange={e => { setDraft({ ...draft, body: e.target.value }); setReviewed(false); }} /></label>
        <label className={styles.field}>번역 확인 목록<textarea disabled={locked} rows={3} value={draft.checklist} onChange={e => { setDraft({ ...draft, checklist: e.target.value }); setReviewed(false); }} /></label>
        <button type="button" disabled={locked || stale || Boolean(conflictId) || !/^[a-z]{2,3}(?:-[A-Z]{2})?$/.test(draft.language) || draft.language.split('-')[0] === 'ko' || !draft.title.trim() || !draft.body.trim() || !draft.checklist.trim()} onClick={() => {
          const content = { title: draft.title.trim(), body: draft.body.trim(), checklist: draft.checklist.split('\n').map(line => line.trim()).filter(Boolean) };
          void mutate(() => draft.editing ? updateTranslation(draft.editing.translation_id, draft.editing.version, content) : createTranslation(kind, sourceId, draft.language, draft.sourceVersion, content), draft.editing?.translation_id);
        }}>번역 초안 저장</button>
        <button type="button" disabled={locked} onClick={() => { setDraft(null); setConflictId(null); setError(null); setReviewed(false); }}>{reviewed ? '편집 종료' : '입력 취소·목록으로'}</button>
      </div> : <button type="button" disabled={locked || !published} onClick={() => { setDraft({ editing: null, language: 'en', title: '', body: '', checklist: '', sourceVersion }); setConflictId(null); }}>+ 수동 번역 작성</button>}
    </div>}
  </section>;
}
