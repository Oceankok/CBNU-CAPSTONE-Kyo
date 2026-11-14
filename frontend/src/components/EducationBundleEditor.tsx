import { useEffect, useMemo, useRef, useState } from 'react';
import ConfirmButton from './ConfirmButton';
import {
  bundleErrorMessage,
  createEducationBundle,
  fetchEducationBundle,
  fetchEducationBundles,
  generateEducationBundle,
  previewEducationBundle,
  publishEducationBundle,
  unpublishEducationBundle,
  updateEducationBundle,
  type BundlePreview,
} from '../api/educationBundles';
import { ApiError } from '../api/client';
import {
  bundleActionBlocked,
  bundleConflictResponseIsCurrent,
  bundleDraftIsDirty,
  bundleInputsDisabled,
  bundleMutationIsAllowed,
  previewIsCurrent,
} from '../api/educationBundleEditorContracts';
import type { EducationBundle } from '../types';
import { formatSeoul } from '../time';
import styles from './EducationBundleEditor.module.css';

const STATUS_LABEL: Record<EducationBundle['status'], string> = {
  draft: '초안',
  published: '게시됨',
  superseded: '교체됨',
};

export default function EducationBundleEditor({
  quarter,
  recommendationIds,
}: {
  quarter: string;
  recommendationIds: string[];
}) {
  const [preview, setPreview] = useState<BundlePreview | null>(null);
  const [bundles, setBundles] = useState<EducationBundle[]>([]);
  const [activeBundle, setActiveBundle] = useState<EducationBundle | null>(null);
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [checklist, setChecklist] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadingBundles, setLoadingBundles] = useState(true);
  const [showBundles, setShowBundles] = useState(false);
  const [previewSelectionKey, setPreviewSelectionKey] = useState<string | null>(null);
  const [conflictBundle, setConflictBundle] = useState<EducationBundle | null>(null);
  const requestRef = useRef(0);
  const conflictEpochRef = useRef(0);
  const activeBundleIdRef = useRef<string | null>(null);
  const selectionKey = recommendationIds.join('|');

  useEffect(() => {
    activeBundleIdRef.current = activeBundle?.bundle_id ?? null;
  }, [activeBundle]);

  const loadBundles = () => {
    const request = ++requestRef.current;
    setLoadingBundles(true);
    return fetchEducationBundles(quarter)
      .then((result) => {
        if (request === requestRef.current) setBundles(result.items);
      })
      .catch((e: unknown) => {
        if (request === requestRef.current) setError(bundleErrorMessage(e));
      })
      .finally(() => {
        if (request === requestRef.current) setLoadingBundles(false);
      });
  };

  useEffect(() => {
    let current = true;
    fetchEducationBundles(quarter)
      .then((result) => {
        if (!current) return;
        setBundles(result.items);
        setLoadingBundles(false);
      })
      .catch((e: unknown) => {
        if (!current) return;
        setError(bundleErrorMessage(e));
        setLoadingBundles(false);
      });
    return () => {
      current = false;
    };
  }, [quarter]);

  const selectedCount = recommendationIds.length;
  const validContent = Boolean(
    title.trim() &&
      body.trim() &&
      checklist
        .split('\n')
        .some((line) => line.trim()),
  );
  const currentPreview = previewIsCurrent(previewSelectionKey, selectionKey)
    ? preview
    : null;
  const selectedSnapshot = currentPreview?.snapshot ?? null;
  const activeDraft = activeBundle ? { title, body, checklist } : null;
  const conflictNeedsReview = Boolean(conflictBundle);
  const activeDirty = Boolean(
    activeBundle && activeDraft && bundleDraftIsDirty(activeBundle, activeDraft),
  );
  const activeActionsBlocked = Boolean(
    activeBundle &&
      activeDraft &&
      bundleActionBlocked(activeBundle, activeDraft, conflictNeedsReview),
  );
  const selectedSources = useMemo(
    () => activeBundle?.sources ?? preview?.sources ?? [],
    [activeBundle, preview],
  );

  const showError = (e: unknown) => setError(bundleErrorMessage(e));

  const isCode = (e: unknown, code: string) => e instanceof ApiError && e.message === code;
  const isVersionConflict = (e: unknown) =>
    e instanceof ApiError &&
    e.status === 409 &&
    ['education_bundle_version_conflict', 'education_bundle_invalid_status', 'education_bundle_publication_conflict'].includes(e.message);

  const refreshSelectionAfterConflict = () => {
    setError('추천이 변경되어 최신 미리보기를 다시 확인해야 합니다. 입력한 내용은 보존됩니다.');
    return previewEducationBundle(recommendationIds)
      .then((next) => {
        setPreview(next);
        setPreviewSelectionKey(selectionKey);
      })
      .catch(showError);
  };

  const refreshBundleAfterConflict = (bundleId: string) => {
    const requestEpoch = ++conflictEpochRef.current;
    setError('다른 관리자가 자료를 변경했습니다. 최신 자료를 확인해 주세요. 입력한 내용은 보존됩니다.');
    return fetchEducationBundle(bundleId)
      .then((latest) => {
        if (!bundleConflictResponseIsCurrent(
          requestEpoch,
          conflictEpochRef.current,
          bundleId,
          activeBundleIdRef.current,
        )) return;
        setActiveBundle(latest);
        activeBundleIdRef.current = latest.bundle_id;
        setConflictBundle(latest);
      })
      .catch((e: unknown) => {
        if (bundleConflictResponseIsCurrent(
          requestEpoch,
          conflictEpochRef.current,
          bundleId,
          activeBundleIdRef.current,
        )) showError(e);
      });
  };

  const previewSelection = () => {
    ++conflictEpochRef.current;
    setBusy(true);
    setError(null);
    previewEducationBundle(recommendationIds)
      .then((next) => {
        setPreview(next);
        setPreviewSelectionKey(selectionKey);
        setActiveBundle(null);
        activeBundleIdRef.current = null;
        setConflictBundle(null);
      })
      .catch(showError)
      .finally(() => setBusy(false));
  };

  const startNewDraft = () => {
    ++conflictEpochRef.current;
    setActiveBundle(null);
    activeBundleIdRef.current = null;
    setConflictBundle(null);
    setPreviewSelectionKey(null);
    setTitle('');
    setBody('');
    setChecklist('');
    setError(null);
  };

  const openBundle = (bundle: EducationBundle) => {
    ++conflictEpochRef.current;
    setActiveBundle(bundle);
    activeBundleIdRef.current = bundle.bundle_id;
    setPreview(null);
    setPreviewSelectionKey(null);
    setConflictBundle(null);
    setTitle(bundle.title);
    setBody(bundle.body);
    setChecklist(bundle.checklist.join('\n'));
    setError(null);
  };

  const save = () => {
    if (!validContent) return;
    const content = {
      title: title.trim(),
      body: body.trim(),
      checklist: checklist
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean),
    };
    setBusy(true);
    setError(null);
    const request = activeBundle
      ? updateEducationBundle(activeBundle.bundle_id, content, activeBundle.version)
      : selectedSnapshot
        ? createEducationBundle(recommendationIds, selectedSnapshot, content)
        : Promise.reject(new Error('먼저 미리보기를 조회해 주세요.'));
    request
      .then((next) => {
        setActiveBundle(next);
        activeBundleIdRef.current = next.bundle_id;
        setConflictBundle(null);
        setPreview(null);
        setPreviewSelectionKey(null);
        setTitle(next.title);
        setBody(next.body);
        setChecklist(next.checklist.join('\n'));
        return loadBundles();
      })
      .catch((e: unknown) => {
        if (isCode(e, 'education_bundle_selection_changed')) {
          void refreshSelectionAfterConflict();
        } else if (isVersionConflict(e) && activeBundle) {
          return refreshBundleAfterConflict(activeBundle.bundle_id);
        } else {
          showError(e);
        }
      })
      .finally(() => setBusy(false));
  };

  const generate = () => {
    if (!selectedSnapshot) return;
    setBusy(true);
    setError(null);
    generateEducationBundle(recommendationIds, selectedSnapshot)
      .then((next) => {
        setActiveBundle(next);
        activeBundleIdRef.current = next.bundle_id;
        setConflictBundle(null);
        setPreview(null);
        setPreviewSelectionKey(null);
        setTitle(next.title);
        setBody(next.body);
        setChecklist(next.checklist.join('\n'));
        return loadBundles();
      })
      .catch((e: unknown) => {
        if (isCode(e, 'education_bundle_selection_changed')) {
          void refreshSelectionAfterConflict();
        } else {
          showError(e);
        }
      })
      .finally(() => setBusy(false));
  };

  const canMutateBundle = (bundle: EducationBundle) => {
    const editingThisBundle = activeBundle?.bundle_id === bundle.bundle_id;
    const blocked = editingThisBundle && activeActionsBlocked;
    if (!bundleMutationIsAllowed(
      bundle.bundle_id,
      bundle.version,
      activeBundle?.bundle_id ?? null,
      activeBundle?.version ?? null,
      blocked,
    )) {
      setError('현재 입력 또는 최신 자료를 먼저 검토하고 명시적으로 해결해 주세요.');
      return false;
    }
    return true;
  };

  const publish = (bundle: EducationBundle) => {
    if (!canMutateBundle(bundle) || bundle.status !== 'draft') return;
    setBusy(true);
    setError(null);
    publishEducationBundle(bundle.bundle_id, bundle.version)
      .then((next) => {
        if (activeBundle?.bundle_id === bundle.bundle_id) {
          setActiveBundle(next);
          activeBundleIdRef.current = next.bundle_id;
          setConflictBundle(null);
        }
        return loadBundles();
      })
      .catch((e: unknown) => {
        if (isVersionConflict(e)) {
          return refreshBundleAfterConflict(bundle.bundle_id);
        } else {
          showError(e);
        }
      })
      .finally(() => setBusy(false));
  };

  const unpublish = (bundle: EducationBundle) => {
    if (!canMutateBundle(bundle) || bundle.status !== 'published') return;
    setBusy(true);
    setError(null);
    unpublishEducationBundle(bundle.bundle_id, bundle.version)
      .then((next) => {
        if (activeBundle?.bundle_id === bundle.bundle_id) {
          setActiveBundle(next);
          activeBundleIdRef.current = next.bundle_id;
          setConflictBundle(null);
        }
        return loadBundles();
      })
      .catch((e: unknown) => {
        if (isVersionConflict(e)) {
          return refreshBundleAfterConflict(bundle.bundle_id);
        } else {
          showError(e);
        }
      })
      .finally(() => setBusy(false));
  };

  return (
    <section className={styles.wrap} aria-labelledby="education-bundle-title">
      <div className={styles.heading}>
        <div>
          <h2 id="education-bundle-title" className={styles.title}>
            다중 구역 통합 교육자료
          </h2>
          <p className={styles.help}>
            같은 분기·같은 PPE의 서로 다른 2~20개 구역 추천을 하나의 공통 자료로 검토합니다.
          </p>
        </div>
        <button
          type="button"
          className={styles.secondary}
          onClick={() => setShowBundles((current) => !current)}
        >
          {showBundles ? '게시자료 목록 닫기' : `게시자료 목록 (${bundles.length})`}
        </button>
      </div>

      {showBundles && (
        <div className={styles.savedList}>
          {loadingBundles && <p className={styles.help}>자료를 불러오는 중...</p>}
          {!loadingBundles && bundles.length === 0 && (
            <p className={styles.help}>이 분기에 저장된 통합 자료가 없습니다.</p>
          )}
          {bundles.map((bundle) => (
            <article key={bundle.bundle_id} className={styles.savedItem}>
              <div className={styles.savedHead}>
                <span className={`${styles.badge} ${styles[bundle.status]}`}>
                  {STATUS_LABEL[bundle.status]}
                </span>
                <strong>{bundle.title || '제목 없음'}</strong>
                <span className={styles.meta}>v{bundle.version}</span>
              </div>
              <p className={styles.meta}>
                {bundle.sources.map((source) => source.zone_name).join(', ')} · {bundle.ppe_type} ·{' '}
                {formatSeoul(bundle.updated_at)}
              </p>
              <div className={styles.actions}>
                <button type="button" className={styles.secondary} disabled={busy} onClick={() => openBundle(bundle)}>
                  검토·편집
                </button>
                {bundle.status === 'draft' && (
                  <ConfirmButton
                    className={styles.primary}
                    label="명시적으로 게시"
                    question="검토한 공통 본문을 작업자에게 게시하시겠습니까?"
                    confirmLabel="게시"
                    danger={false}
                    disabled={busy || (activeBundle?.bundle_id === bundle.bundle_id && activeActionsBlocked)}
                    onConfirm={() => publish(bundle)}
                  />
                )}
                {bundle.status === 'published' && (
                  <ConfirmButton
                    className={styles.secondary}
                    label="게시 취소"
                    question="게시를 취소하면 작업자는 이 자료를 더 이상 볼 수 없습니다."
                    confirmLabel="게시 취소"
                    disabled={busy || (activeBundle?.bundle_id === bundle.bundle_id && activeActionsBlocked)}
                    onConfirm={() => unpublish(bundle)}
                  />
                )}
              </div>
            </article>
          ))}
        </div>
      )}

      {selectedCount < 2 ? (
        <p className={styles.selectionHint}>
          추천 카드에서 함께 묶을 구역을 2개 이상 선택하면 미리보기를 시작할 수 있습니다.
        </p>
      ) : (
        <div className={styles.selectionBar}>
          <strong>{selectedCount}개 구역 선택됨</strong>
          <span>선택을 바꾸면 이전 snapshot은 다시 사용하지 않습니다.</span>
          <button type="button" className={styles.primary} disabled={busy} onClick={previewSelection}>
            미리보기 조회
          </button>
        </div>
      )}

      {preview && !currentPreview && (
        <p className={styles.warning}>
          선택이 변경되어 이전 snapshot을 사용할 수 없습니다. 입력한 초안은 보존되어 있으니 최신 미리보기를 다시 조회해 확인하세요.
        </p>
      )}

      {currentPreview && preview && (
        <div className={styles.preview}>
          <div className={styles.previewHead}>
            <strong>원본 추천 미리보기</strong>
            <span>{preview.ppe_type} · {preview.quarter}</span>
          </div>
          <div className={styles.sourceList}>
            {currentPreview.sources.map((source) => (
              <div key={source.recommendation_id} className={styles.source}>
                <strong>{source.zone_name}</strong>
                <span>{source.confirmed_count}건 · 점수 {source.priority_score.toFixed(1)}</span>
                <span className={styles.topic}>{source.education_topic}</span>
              </div>
            ))}
          </div>
          <p className={styles.note}>
            확정 건수 합계 {preview.confirmed_count_sum}건 · 서로 다른 사람 수나 중복 제거 수가 아닙니다.
          </p>
          <p className={styles.snapshot}>snapshot: {currentPreview.snapshot}</p>
          <p className={styles.warning}>
            AI 초안은 일반 PPE 교육만 작성합니다. 구역별 특수 지침은 관리자가 직접 보완하고, 검토 후 명시적으로 게시하세요.
          </p>
        </div>
      )}

      {activeBundle && (
        <div className={styles.preview}>
          <div className={styles.previewHead}>
            <strong>통합 자료 검토</strong>
            <span>{STATUS_LABEL[activeBundle.status]} · v{activeBundle.version}</span>
          </div>
          <p className={styles.note}>
            대상 구역 중 하나에 배정된 작업자는 공통 본문 전체를 읽습니다. 비공개 구역 정보는 포함하지 않았는지 확인하세요.
          </p>
          <div className={styles.sourceList}>
            {selectedSources.map((source) => (
              <span key={source.recommendation_id} className={styles.sourceTag}>{source.zone_name}</span>
            ))}
          </div>
        </div>
      )}

      {conflictBundle && activeBundle?.bundle_id === conflictBundle.bundle_id && (
        <div className={styles.conflict} role="alert">
          <strong>최신 서버 자료와 충돌했습니다.</strong>
          <p>현재 입력은 보존했으며 자동 저장·게시하지 않았습니다. 아래 최신 서버본을 검토한 뒤 명시적으로 선택하세요.</p>
          <p><b>서버 제목:</b> {conflictBundle.title}</p>
          <p className={styles.serverBody}><b>서버 본문:</b> {conflictBundle.body}</p>
          <div className={styles.actions}>
            <button
              type="button"
              className={styles.secondary}
              onClick={() => {
                setTitle(conflictBundle.title);
                setBody(conflictBundle.body);
                setChecklist(conflictBundle.checklist.join('\n'));
                setConflictBundle(null);
                setError(null);
              }}
            >
              최신 서버본으로 다시 시작
            </button>
            <button
              type="button"
              className={styles.primary}
              onClick={() => {
                setConflictBundle(null);
                setError(null);
              }}
            >
              내 초안으로 해결
            </button>
          </div>
        </div>
      )}

      {activeBundle && activeDirty && !conflictNeedsReview && (
        <p className={styles.warning}>
          저장하지 않은 수정이 있습니다. 현재 화면의 수정본은 저장한 뒤 다시 검토해야 게시할 수 있습니다.
        </p>
      )}

      {(preview || activeBundle) && (
        <div className={styles.form}>
          <label className={styles.field}>
            제목
            <input className={styles.input} disabled={bundleInputsDisabled(busy)} maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label className={styles.field}>
            공통 교육 본문
            <textarea className={styles.input} disabled={bundleInputsDisabled(busy)} rows={6} maxLength={12000} value={body} onChange={(e) => setBody(e.target.value)} />
          </label>
          <label className={styles.field}>
            확인 체크리스트 (한 줄에 하나)
            <textarea className={styles.input} disabled={bundleInputsDisabled(busy)} rows={4} value={checklist} onChange={(e) => setChecklist(e.target.value)} />
          </label>
          <div className={styles.actions}>
            {activeBundle ? (
              <button type="button" className={styles.primary} disabled={busy || !validContent || activeBundle.status !== 'draft' || conflictNeedsReview} onClick={save}>
                초안 저장
              </button>
            ) : (
              <>
                <button type="button" className={styles.primary} disabled={busy || !validContent} onClick={save}>
                  수동 초안 저장
                </button>
                <ConfirmButton
                  className={styles.secondary}
                  label="AI 초안 생성 요청"
                  question="AI가 일반 PPE 교육 초안을 생성합니다. 생성 후에도 관리자의 검토와 명시적 게시가 필요합니다."
                  confirmLabel="생성"
                  danger={false}
                  disabled={busy || conflictNeedsReview}
                  onConfirm={generate}
                />
              </>
            )}
            {activeBundle && activeBundle.status === 'draft' && (
              <ConfirmButton
                className={styles.primary}
                label="검토 후 게시"
                question="현재 버전의 공통 교육자료를 작업자에게 게시하시겠습니까?"
                confirmLabel="게시"
                danger={false}
                disabled={busy || activeActionsBlocked}
                onConfirm={() => publish(activeBundle)}
              />
            )}
            {activeBundle && activeBundle.status === 'published' && (
              <ConfirmButton
                className={styles.secondary}
                label="게시 취소"
                question="게시를 취소하면 작업자는 이 자료를 더 이상 볼 수 없습니다."
                confirmLabel="게시 취소"
                disabled={busy || activeActionsBlocked}
                onConfirm={() => unpublish(activeBundle)}
              />
            )}
            {activeBundle && <button type="button" className={styles.secondary} disabled={busy} onClick={startNewDraft}>새 선택으로 작성</button>}
          </div>
        </div>
      )}

      {error && <p className={styles.error} role="alert">{error}</p>}
    </section>
  );
}
