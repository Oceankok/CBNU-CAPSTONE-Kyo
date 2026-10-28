import { useState } from 'react';
import ConfirmButton from './ConfirmButton';
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
import type { EducationMaterial, GenerationStatus } from '../api/materials';
import styles from './EducationMaterialEditor.module.css';

const STATUS_LABEL = {
  draft: '초안',
  published: '게시됨',
  superseded: '교체됨',
} as const;

// Admin writes, reviews and publishes the education text for one recommendation (#111).
// Workers only see a material once it is published.
export default function EducationMaterialEditor({
  recommendationId,
}: {
  recommendationId: string;
}) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<EducationMaterial[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [generation, setGeneration] = useState<GenerationStatus | null>(null);
  const [generating, setGenerating] = useState(false);
  // null: not editing; 'new': writing a new draft; otherwise the draft being edited
  const [editing, setEditing] = useState<EducationMaterial | 'new' | null>(
    null,
  );
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [checklist, setChecklist] = useState('');

  const fail = (e: Error) => setError(MATERIAL_ERRORS[e.message] ?? e.message);

  const load = () =>
    fetchMaterials(recommendationId)
      .then((r) => setItems(r.items))
      .catch(fail);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && !items) void load();
    if (next && !generation)
      fetchGenerationStatus()
        .then(setGeneration)
        .catch(() => {});
  };

  // Drafts only; the admin still edits, checks the facts and approves before workers see it
  const generate = () => {
    setGenerating(true);
    setError(null);
    generateMaterial(recommendationId)
      .then(() => load())
      .catch(fail)
      .finally(() => setGenerating(false));
  };

  const startEdit = (m: EducationMaterial | 'new') => {
    setEditing(m);
    setError(null);
    setTitle(m === 'new' ? '' : m.title);
    setBody(m === 'new' ? '' : m.body);
    setChecklist(m === 'new' ? '' : m.checklist.join('\n'));
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
    if (!editing) return;
    setBusy(true);
    setError(null);
    const request =
      editing === 'new'
        ? createMaterial(recommendationId, content())
        : updateMaterial(editing.material_id, content(), editing.version);
    request
      .then(() => {
        setEditing(null);
        return load();
      })
      .catch(fail)
      .finally(() => setBusy(false));
  };

  const publish = (m: EducationMaterial) => {
    setBusy(true);
    setError(null);
    publishMaterial(m.material_id, m.version)
      .then(() => load())
      .catch(fail)
      .finally(() => setBusy(false));
  };

  const unpublish = (m: EducationMaterial) => {
    setBusy(true);
    setError(null);
    unpublishMaterial(m.material_id, m.version)
      .then(() => load())
      .catch(fail)
      .finally(() => setBusy(false));
  };

  return (
    <div className={styles.wrap}>
      <button type="button" className={styles.toggle} onClick={toggle}>
        {open ? '교육 자료 닫기' : '교육 자료 작성·게시'}
      </button>

      {open && (
        <div className={styles.panel}>
          <p className={styles.help}>
            작업자에게는 게시한 자료만 보입니다. 안전 지침의 정확성은 게시 전에
            관리자가 직접 확인합니다.
          </p>
          {error && <p className={styles.error}>⚠ {error}</p>}
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
                {new Date(m.updated_at).toLocaleString('ko-KR')}
                {m.published_at &&
                  ` · 게시 ${m.published_by} ${new Date(m.published_at).toLocaleString('ko-KR')}`}
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
                    disabled={busy}
                    onConfirm={() => unpublish(m)}
                  />
                </div>
              )}
              {m.status === 'draft' && editing !== m && (
                <div className={styles.actions}>
                  <button
                    type="button"
                    className={styles.btn}
                    disabled={busy}
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
                    disabled={busy}
                    onConfirm={() => publish(m)}
                  />
                </div>
              )}
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
                  className={styles.input}
                  value={title}
                  maxLength={200}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </label>
              <label className={styles.field}>
                본문
                <textarea
                  className={styles.input}
                  rows={5}
                  value={body}
                  maxLength={12000}
                  onChange={(e) => setBody(e.target.value)}
                />
              </label>
              <label className={styles.field}>
                확인 목록 (한 줄에 하나)
                <textarea
                  className={styles.input}
                  rows={4}
                  value={checklist}
                  onChange={(e) => setChecklist(e.target.value)}
                />
              </label>
              <div className={styles.actions}>
                <button
                  type="button"
                  className={styles.btn}
                  onClick={() => setEditing(null)}
                >
                  취소
                </button>
                <button
                  type="button"
                  className={styles.primary}
                  disabled={busy || !valid}
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
