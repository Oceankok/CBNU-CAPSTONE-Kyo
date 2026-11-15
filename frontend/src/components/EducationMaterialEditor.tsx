import { useEffect, useRef, useState } from 'react';
import ConfirmButton from './ConfirmButton';
import EducationTranslationEditor from './EducationTranslationEditor';
import {
  MATERIAL_ERRORS,
  createMaterial,
  fetchGenerationStatus,
  fetchMaterials,
  generateMaterial,
  publishMaterial,
  unpublishMaterial,
  updateMaterial,
} from '../api/materials';
import type {
  EducationMaterial,
  EducationMaterialScope,
  GenerationStatus,
} from '../api/materials';
import { ApiError } from '../api/client';
import { formatSeoul } from '../time';
import styles from './EducationMaterialEditor.module.css';

export interface EducationMaterialDraft {
  editing: EducationMaterial | 'new';
  editingScope: EducationMaterialScope;
  title: string;
  body: string;
  checklist: string;
}

const STATUS_LABEL = {
  draft: '초안',
  published: '게시됨',
  superseded: '교체됨',
} as const;

// Admin writes, reviews and publishes the education text for one recommendation (#111).
// Workers only see a material once it is published.
export default function EducationMaterialEditor({
  recommendationId,
  scope,
  draft,
  onDraftChange,
  onScopeConflict,
}: {
  recommendationId: string;
  scope: EducationMaterialScope;
  draft?: EducationMaterialDraft;
  onDraftChange?: (draft: EducationMaterialDraft | null) => void;
  onScopeConflict?: (scope: EducationMaterialScope) => void;
}) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<EducationMaterial[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [generation, setGeneration] = useState<GenerationStatus | null>(null);
  const [generating, setGenerating] = useState(false);
  // null: not editing; 'new': writing a new draft; otherwise the draft being edited
  const [editing, setEditing] = useState<EducationMaterial | 'new' | null>(
    draft?.editing ?? null,
  );
  const [editingScope, setEditingScope] = useState<EducationMaterialScope | null>(
    draft?.editingScope ?? null,
  );
  const [title, setTitle] = useState(draft?.title ?? '');
  const [body, setBody] = useState(draft?.body ?? '');
  const [checklist, setChecklist] = useState(draft?.checklist ?? '');
  const loadRequest = useRef(0);
  const scopeConflictRequest = useRef(0);
  const draftRevision = useRef(0);
  const [conflict, setConflict] = useState<EducationMaterial | null>(null);
  const [reviewRequired, setReviewRequired] = useState<string | null>(null);
  const locked = busy || generating;
  const editingId = editing && editing !== 'new' ? editing.material_id : null;

  // A response belongs to the draft that submitted it, including across unmounts.
  useEffect(() => () => { ++draftRevision.current; }, []);

  const isScopeConflict = (e: unknown) => e instanceof ApiError &&
    ((e.status === 409 && e.message === 'education_recommendation_changed') ||
      (e.status === 404 && e.message === 'recommendation_not_found'));

  const fail = (e: Error) => setError(MATERIAL_ERRORS[e.message] ?? e.message);

  const load = () => {
    const request = ++loadRequest.current;
    return fetchMaterials(recommendationId)
      .then((r) => {
        if (request === loadRequest.current) setItems(r.items);
        return r;
      })
      .catch((e) => {
        if (request === loadRequest.current) fail(e);
        throw e;
      });
  };

  const refreshAfterScopeConflict = async () => {
    const request = ++scopeConflictRequest.current;
    onScopeConflict?.(structuredClone(editingScope ?? scope));
    try {
      await load();
      if (request !== scopeConflictRequest.current) return;
      setError(
        '추천 대상이 바뀌었습니다. 작성 중인 내용은 보존했습니다. 최신 자료 목록을 확인한 뒤 대상을 명시적으로 다시 선택하세요.',
      );
    } catch (e) {
      if (request !== scopeConflictRequest.current) return;
      setError(
        `추천 대상이 바뀌었고 최신 자료 목록을 불러오지 못했습니다. 작성 중인 내용은 보존했습니다. (${e instanceof Error ? e.message : String(e)})`,
      );
    }
  };

  const refreshMaterialAfterConflict = async (materialId: string) => {
    const revision = draftRevision.current;
    setReviewRequired(materialId);
    setError('다른 관리자가 자료를 변경했습니다. 입력을 보존했습니다. 최신 서버본을 확인한 뒤 명시적으로 선택하세요.');
    try {
      const result = await load();
      if (revision !== draftRevision.current) return;
      const latest = result.items.find((item) => item.material_id === materialId);
      setConflict(latest ?? null);
      if (!latest) setError('최신 자료를 목록에서 찾지 못했습니다. 입력은 보존됩니다. 다시 조회하거나 다른 자료를 선택하세요.');
    } catch {
      // Keep saving blocked until the latest version can be reviewed.
      if (revision === draftRevision.current) setError('최신 자료 조회에 실패했습니다. 입력을 보존했습니다. 다시 조회하세요.');
    }
  };

  const handleMutationError = (e: unknown, materialId?: string) => {
    if (e instanceof ApiError && e.status === 409 && materialId &&
      ['education_material_version_conflict', 'published_material_read_only',
        'education_material_not_draft', 'education_material_not_published'].includes(e.message)) {
      return refreshMaterialAfterConflict(materialId);
    }
    if (isScopeConflict(e)) return refreshAfterScopeConflict();
    fail(e instanceof Error ? e : new Error(String(e)));
  };

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && !items) void load().catch(() => {});
    if (next && !generation)
      fetchGenerationStatus()
        .then(setGeneration)
        .catch(() => {});
  };

  // Drafts only; the admin still edits, checks the facts and approves before workers see it
  const generate = () => {
    if (locked) return;
    setGenerating(true);
    setError(null);
    generateMaterial(recommendationId, structuredClone(scope))
      .then(() => load())
      .catch((e: unknown) => {
        if (isScopeConflict(e)) {
          return refreshAfterScopeConflict();
        } else {
          fail(e instanceof Error ? e : new Error(String(e)));
        }
      })
      .finally(() => setGenerating(false));
  };

  const startEdit = (m: EducationMaterial | 'new') => {
    if (locked) return;
    ++draftRevision.current;
    setConflict(null);
    setReviewRequired(null);
    const nextScope = structuredClone(scope);
    const nextTitle = m === 'new' ? '' : m.title;
    const nextBody = m === 'new' ? '' : m.body;
    const nextChecklist = m === 'new' ? '' : m.checklist.join('\n');
    setEditing(m);
    setEditingScope(nextScope);
    setError(null);
    setTitle(nextTitle);
    setBody(nextBody);
    setChecklist(nextChecklist);
    onDraftChange?.({
      editing: m,
      editingScope: nextScope,
      title: nextTitle,
      body: nextBody,
      checklist: nextChecklist,
    });
  };

  const updateDraft = (
    patch: Partial<Pick<EducationMaterialDraft, 'title' | 'body' | 'checklist'>>,
  ) => {
    if (!editing || locked) return;
    ++draftRevision.current;
    onDraftChange?.({
      editing,
      editingScope: editingScope ?? structuredClone(scope),
      title,
      body,
      checklist,
      ...patch,
    });
  };

  const content = () => ({
    title: title.trim(),
    body: body.trim(),
    checklist: checklist
      .split('\n')
      .map((l) => l.trim())
      .filter(Boolean),
  });
  const valid =
    title.trim() && body.trim() && checklist.split('\n').some((l) => l.trim());

  const save = () => {
    if (!editing || locked || (reviewRequired && reviewRequired === editingId) || !valid) return;
    const revision = draftRevision.current;
    setBusy(true);
    setError(null);
    const request =
      editing === 'new'
        ? createMaterial(
            recommendationId,
            content(),
            editingScope ?? structuredClone(scope),
          )
        : updateMaterial(editing.material_id, content(), editing.version);
    request
      .then(() => {
        if (revision === draftRevision.current) {
          setEditing(null);
          onDraftChange?.(null);
        }
        return load();
      })
      .catch((e: unknown) => {
        if (revision !== draftRevision.current) return;
        return handleMutationError(e, editing === 'new' ? undefined : editing.material_id);
      })
      .finally(() => setBusy(false));
  };

  const publish = (m: EducationMaterial) => {
    if (locked || editingId === m.material_id || reviewRequired === m.material_id) return;
    setBusy(true);
    setError(null);
    publishMaterial(m.material_id, m.version)
      .then(() => load())
      .catch((e: unknown) => handleMutationError(e, m.material_id))
      .finally(() => setBusy(false));
  };

  const unpublish = (m: EducationMaterial) => {
    if (locked || editingId === m.material_id || reviewRequired === m.material_id) return;
    setBusy(true);
    setError(null);
    unpublishMaterial(m.material_id, m.version)
      .then(() => load())
      .catch((e: unknown) => handleMutationError(e, m.material_id))
      .finally(() => setBusy(false));
  };

  return (
    <div className={styles.wrap}>
      <button type="button" className={styles.toggle} disabled={locked} onClick={toggle}>
        {open ? '교육 자료 닫기' : '교육 자료 작성·게시'}
      </button>

      {open && (
        <div className={styles.panel}>
          <p className={styles.help}>
            작업자에게는 게시한 자료만 보입니다. 안전 지침의 정확성은 게시 전에
            관리자가 직접 확인합니다.
          </p>
          {error && <p className={styles.error}>⚠ {error}</p>}
          {reviewRequired && (
            <div className={styles.preview} role="alert">
              {conflict ? (
                <>
                  <strong>최신 서버본 · v{conflict.version} · {STATUS_LABEL[conflict.status]}</strong>
                  <p>{conflict.title}</p>
                  <p className={styles.bodyText}>{conflict.body}</p>
                  <ul>{conflict.checklist.map((item, index) => <li key={index}>{item}</li>)}</ul>
                  {editingId === conflict.material_id ? (
                    <>
                      <button type="button" disabled={locked || conflict.status !== 'draft'} onClick={() => startEdit(conflict)}>
                        최신 서버본으로 다시 시작
                      </button>
                      <button type="button" disabled={locked || conflict.status !== 'draft'} onClick={() => {
                        ++draftRevision.current;
                        setEditing(conflict);
                        onDraftChange?.({ editing: conflict, editingScope: editingScope ?? structuredClone(scope), title, body, checklist });
                        setConflict(null);
                        setReviewRequired(null);
                        setError(null);
                      }}>
                        내 입력을 유지하고 최신 버전 선택
                      </button>
                      {conflict.status !== 'draft' && <p>현재 서버본은 수정할 수 없습니다. 입력을 복사하거나 다른 초안을 선택하세요.</p>}
                    </>
                  ) : (
                    <button type="button" disabled={locked} onClick={() => {
                      setConflict(null);
                      setReviewRequired(null);
                      setError(null);
                    }}>최신 자료 확인 완료</button>
                  )}
                </>
              ) : (
                <button type="button" disabled={locked} onClick={() => {
                  setBusy(true);
                  void refreshMaterialAfterConflict(reviewRequired).finally(() => setBusy(false));
                }}>최신 자료 다시 조회</button>
              )}
            </div>
          )}
          {!items && !error && <p className={styles.help}>불러오는 중...</p>}

          {items?.map((m) => (
            <div key={m.material_id} className={styles.item}>
              <div className={styles.itemHead}>
                <span className={`${styles.badge} ${styles[m.status]}`}>
                  {STATUS_LABEL[m.status]}
                </span>
                {m.provider !== 'manual' && (
                  <span className={styles.ai}>AI 초안</span>
                )}
                <strong className={styles.itemTitle}>{m.title}</strong>
              </div>
              <p className={styles.meta}>
                {m.updated_by} ·{' '}
                {formatSeoul(m.updated_at)}
                {m.published_at &&
                  ` · 게시 ${m.published_by} ${formatSeoul(m.published_at)}`}
              </p>
              <details className={styles.preview}>
                <summary>내용 보기</summary>
                <p className={styles.bodyText}>{m.body}</p>
                <ul>
                  {m.checklist.map((c, i) => (
                    <li key={i}>{c}</li>
                  ))}
                </ul>
              </details>
              {m.status === 'published' && (
                <div className={styles.actions}>
                  <ConfirmButton
                    className={styles.btn}
                    label="게시 취소"
                    question="작업자에게 더 이상 보이지 않고 초안으로 돌아갑니다. 이전에 교체된 자료는 복구되지 않습니다."
                    confirmLabel="게시 취소"
                    disabled={locked || editingId === m.material_id || reviewRequired === m.material_id}
                    onConfirm={() => unpublish(m)}
                  />
                </div>
              )}
              {m.status === 'draft' && editingId !== m.material_id && (
                <div className={styles.actions}>
                  <button
                    type="button"
                    className={styles.btn}
                    disabled={locked}
                    onClick={() => startEdit(m)}
                  >
                    수정
                  </button>
                  <ConfirmButton
                    className={styles.primary}
                    label="검토 완료 · 게시"
                    question="게시하면 작업자에게 바로 보이고, 같은 구역의 이전 자료는 교체됩니다."
                    confirmLabel="게시"
                    danger={false}
                    disabled={locked || reviewRequired === m.material_id}
                    onConfirm={() => publish(m)}
                  />
                </div>
              )}
              <EducationTranslationEditor kind="material" sourceId={m.material_id} sourceVersion={m.version} published={m.status === 'published'} disabled={locked} />
            </div>
          ))}
          {items?.length === 0 && !editing && (
            <p className={styles.help}>아직 작성한 자료가 없습니다.</p>
          )}

          {editing ? (
            <div className={styles.form}>
              <label className={styles.field}>
                제목
                <input
                  disabled={locked}
                  className={styles.input}
                  value={title}
                  maxLength={200}
                  onChange={(e) => {
                    setTitle(e.target.value);
                    updateDraft({ title: e.target.value });
                  }}
                />
              </label>
              <label className={styles.field}>
                본문
                <textarea
                  disabled={locked}
                  className={styles.input}
                  rows={5}
                  value={body}
                  maxLength={12000}
                  onChange={(e) => {
                    setBody(e.target.value);
                    updateDraft({ body: e.target.value });
                  }}
                />
              </label>
              <label className={styles.field}>
                확인 목록 (한 줄에 하나)
                <textarea
                  disabled={locked}
                  className={styles.input}
                  rows={4}
                  value={checklist}
                  onChange={(e) => {
                    setChecklist(e.target.value);
                    updateDraft({ checklist: e.target.value });
                  }}
                />
              </label>
              <div className={styles.actions}>
                <button
                  type="button"
                  className={styles.btn}
                  disabled={locked}
                  onClick={() => {
                    ++draftRevision.current;
                    setEditing(null);
                    setConflict(null);
                    setReviewRequired(null);
                    onDraftChange?.(null);
                  }}
                >
                  취소
                </button>
                <button
                  type="button"
                  className={styles.primary}
                  disabled={locked || !valid || Boolean(reviewRequired && reviewRequired === editingId)}
                  onClick={save}
                >
                  {editing === 'new' ? '초안 저장' : '수정 저장'}
                </button>
              </div>
            </div>
          ) : (
            <div className={styles.actions}>
              <button
                type="button"
                className={styles.btn}
                disabled={locked}
                onClick={() => startEdit('new')}
              >
                + 새 초안 작성
              </button>
              <ConfirmButton
                className={styles.btn}
                label={generating ? 'AI가 쓰는 중…' : 'AI로 초안 만들기'}
                question="외부 AI 서비스를 호출하며 비용이 들 수 있습니다. 추천 주제와 PPE 종류만 전송합니다."
                confirmLabel="생성"
                danger={false}
                disabled={busy || generating || !generation?.configured}
                onConfirm={generate}
              />
              {generation && !generation.configured && (
                <span className={styles.help}>
                  AI 생성은 서버에 설정되지 않아 쓸 수 없습니다.
                </span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
