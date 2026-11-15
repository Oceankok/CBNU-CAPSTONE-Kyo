import { useState, useEffect, useRef } from 'react';
import { useOutletContext } from 'react-router-dom';
import {
  fetchRecommendations,
  generateRecommendations,
} from '../api/recommendations';
import type {
  EducationRecommendation,
  EducationRecommendationList,
} from '../types';
import { ApiError } from '../api/client';
import { isLatestRequest } from '../api/schema31Contracts';
import { refreshAfterScopeConflict as refreshLatestAfterScopeConflict } from '../api/schema31Interactions';
import EducationMaterialEditor from '../components/EducationMaterialEditor';
import type { EducationMaterialDraft } from '../components/EducationMaterialEditor';
import { fetchEducationBundleTargets, type EducationBundleTargets } from '../api/educationBundles';
import EducationBundleEditor from '../components/EducationBundleEditor';
import type { EducationMaterialScope } from '../api/materials';
import type { LayoutContext } from '../components/AppLayout';
import styles from './RecommendPage.module.css';

const PPE_LABEL: Record<string, string> = {
  helmet: '안전모',
  vest: '안전조끼',
};

// Color per rank: 1st red, 2nd orange, 3rd yellow-brown
const RANK_COLORS = ['#e53e3e', '#dd6b20', '#d69e2e'];

function ScoreRow({ label, value }: { label: string; value: string | number }) {
  return (
    <div className={styles.scoreRow}>
      <span className={styles.scoreLabel}>{label}</span>
      <span className={styles.scoreValue}>{value}</span>
    </div>
  );
}

function RecommendCard({
  item,
  quarter,
  draft,
  onDraftChange,
  onScopeConflict,
}: {
  item: EducationRecommendation;
  quarter: string;
  draft?: EducationMaterialDraft;
  onDraftChange: (draft: EducationMaterialDraft | null) => void;
  onScopeConflict: (scope: EducationMaterialScope) => void;
}) {
  const rankColor = RANK_COLORS[item.recommendation_rank - 1] ?? '#718096';
  return (
    <div className={styles.card}>
      <div className={styles.cardHeader}>
        <span className={styles.rank} style={{ background: rankColor }}>
          {item.recommendation_rank}순위
        </span>
        <div className={styles.tags}>
          <span className={styles.tag}>
            {PPE_LABEL[item.ppe_type] ?? item.ppe_type}
          </span>
          <span className={styles.tag}>{item.zone_name}</span>
        </div>
      </div>

      <p className={styles.topic}>{item.education_topic}</p>

      {/* Scoring detail is supporting info — collapsed by default */}
      <details className={styles.breakdown}>
        <summary className={styles.breakdownTitle}>
          우선순위 점수 {item.priority_score.toFixed(1)}점 · 산정 근거
        </summary>
        <ScoreRow
          label="확정 위반 건수"
          value={`${item.score_breakdown.confirmed_count}건`}
        />
        {item.score_version === 'zone_ppe_v2' ? (
          <>
            <ScoreRow
              label="발생한 주"
              value={`${item.score_breakdown.repeat_weeks}주 / 분기 ${item.score_breakdown.period_weeks ?? '-'}주`}
            />
            <ScoreRow
              label="구역 위험도 배율"
              value={`${item.score_breakdown.process_risk_weight.toFixed(2)}배`}
            />
            <p className={styles.scoreNote}>
              점수 = 확정 건수 × (1 + 0.2 × 발생한 주 비율) × 위험도 배율. 교육
              순서를 정하는 상대 점수이며 확률이나 정확도가 아닙니다.
            </p>
          </>
        ) : (
          <>
            <ScoreRow
              label="반복 발생 주수"
              value={`${item.score_breakdown.repeat_weeks}주`}
            />
            <ScoreRow
              label="구역 집중도"
              value={
                (item.score_breakdown.zone_concentration * 100).toFixed(0) + '%'
              }
            />
            <ScoreRow
              label="공정 위험 가중치"
              value={item.score_breakdown.process_risk_weight.toFixed(1)}
            />
            {item.score_version === 'legacy_unversioned' && (
              <p className={styles.scoreNote}>
                이전 방식으로 계산한 점수입니다. 새 방식 점수와 직접 비교할 수
                없습니다.
              </p>
            )}
          </>
        )}
      </details>

      <EducationMaterialEditor
        recommendationId={item.recommendation_id}
        draft={draft}
        onDraftChange={onDraftChange}
        onScopeConflict={onScopeConflict}
        scope={{
          recommendation_id: item.recommendation_id,
          quarter,
          zone_name: item.zone_name,
          ppe_type: item.ppe_type,
          education_topic: item.education_topic,
        }}
      />
    </div>
  );
}

export default function RecommendPage() {
  const { quarter } = useOutletContext<LayoutContext>();
  const [data, setData] = useState<EducationRecommendationList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [latest, setLatest] = useState<EducationRecommendationList | null>(null);
  const [drafts, setDrafts] = useState<Record<string, EducationMaterialDraft>>({});
  const [selection, setSelection] = useState({ ids: [] as string[], revision: 0 });
  const selectedIds = selection.ids;
  const recommendationRequest = useRef(0);
  const latestRecommendationRequest = useRef(0);
  const [targets, setTargets] = useState<EducationBundleTargets | null>(null);
  const [pendingTargets, setPendingTargets] = useState<EducationBundleTargets | null>(null);
  const [pendingRecommendations, setPendingRecommendations] = useState<EducationRecommendationList | null>(null);
  const [targetsError, setTargetsError] = useState<string | null>(null);
  const [targetsLoading, setTargetsLoading] = useState(true);
  const targetRequest = useRef(0);

  const readTargets = async (regenerate: boolean) => {
    const request = ++targetRequest.current;
    setTargetsLoading(true);
    setTargetsError(null);
    try {
      const recommendations = regenerate ? await generateRecommendations(quarter) : null;
      if (request !== targetRequest.current) return;
      const next = await fetchEducationBundleTargets(quarter);
      if (request !== targetRequest.current) return;
      setPendingRecommendations(recommendations);
      // Never replace a selected pool or a preview silently after a refresh.
      if (targets || selectedIds.length || regenerate) setPendingTargets(next);
      else setTargets(next);
    } catch (e) {
      if (request === targetRequest.current) setTargetsError(e instanceof Error ? e.message : String(e));
    } finally {
      if (request === targetRequest.current) setTargetsLoading(false);
    }
  };

  useEffect(() => {
    const targetEpoch = targetRequest;
    const latestEpoch = latestRecommendationRequest;
    const request = ++targetRequest.current;
    fetchEducationBundleTargets(quarter)
      .then(next => { if (request === targetRequest.current) setTargets(next); })
      .catch((e: unknown) => {
        if (request === targetRequest.current) setTargetsError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => { if (request === targetRequest.current) setTargetsLoading(false); });
    return () => {
      ++targetEpoch.current;
      ++latestEpoch.current;
    };
  }, [quarter]);

  const storeDraft = (recommendationId: string, draft: EducationMaterialDraft | null) => {
    setDrafts((current) => {
      const next = { ...current };
      if (draft) next[recommendationId] = draft;
      else delete next[recommendationId];
      return next;
    });
  };

  const refreshRecommendationsAfterConflict = async (scope: EducationMaterialScope) => {
    const request = ++latestRecommendationRequest.current;
    try {
      const next = await refreshLatestAfterScopeConflict(409, () =>
        fetchRecommendations(scope.quarter),
      );
      if (!isLatestRequest(request, latestRecommendationRequest.current)) return;
      if (next) setLatest(next);
      setError(
        '추천 목록이 변경되었습니다. 입력 중인 초안은 보존했습니다. 최신 추천 대상을 명시적으로 다시 선택하세요.',
      );
    } catch (e) {
      if (!isLatestRequest(request, latestRecommendationRequest.current)) return;
      setError(
        `최신 추천 목록을 불러오지 못했습니다. 기존 초안은 보존되어 있습니다. (${e instanceof Error ? e.message : String(e)})`,
      );
    }
  };

  const selectLatest = (next: EducationRecommendationList, recommendationId?: string) => {
    const selected = next.items.find((item) => item.recommendation_id === recommendationId);
    setData(
      selected
        ? { ...next, items: [selected, ...next.items.filter((item) => item !== selected)] }
        : next,
    );
    // Even unchanged IDs can represent new source data after the explicit refresh.
    setSelection(current => ({
      ids: current.ids,
      revision: current.revision + 1,
    }));
    setLatest(null);
    setError(null);
  };

  const selectedPpe = targets?.items.find((item) => selectedIds.includes(item.recommendation_id))?.ppe_type;
  const selectedZones = new Set(targets?.items.filter(item => selectedIds.includes(item.recommendation_id)).map(item => item.zone_name));
  const toggleSelection = (recommendationId: string, checked: boolean) => {
    setSelection(current => {
      if (checked === current.ids.includes(recommendationId)) return current;
      if (checked && current.ids.length >= 20) return current;
      return {
        ids: checked
          ? [...current.ids, recommendationId]
          : current.ids.filter(id => id !== recommendationId),
        revision: current.revision + 1,
      };
    });
  };

  // AppLayout remounts this page when the quarter changes, so this runs once per quarter
  useEffect(() => {
    const epoch = recommendationRequest;
    const request = ++recommendationRequest.current;
    const noData = `${quarter}에는 추천을 만들 확정 위반 기록이 아직 없습니다.`;
    fetchRecommendations(quarter)
      .then((next) => {
        if (isLatestRequest(request, recommendationRequest.current)) setData(next);
      })
      .catch((e) => {
        if (!isLatestRequest(request, recommendationRequest.current)) return;
        // Only "no recommendations yet" (404) triggers generation. An expired login, a
        // permission or server error, or a network failure must not start a write (#240)
        if (!(e instanceof ApiError && e.status === 404)) {
          setError(e instanceof Error ? e.message : String(e));
          return;
        }
        return generateRecommendations(quarter)
          .then((next) => {
            if (!isLatestRequest(request, recommendationRequest.current)) return;
            setData(next);
            // A newly generated quarter now has a complete saved selection pool.
            const targetRead = ++targetRequest.current;
            return fetchEducationBundleTargets(quarter)
              .then(result => {
                if (targetRead === targetRequest.current) {
                  setTargets(result);
                  setTargetsError(null);
                }
              })
              .catch((e: unknown) => {
                if (targetRead === targetRequest.current) setTargetsError(e instanceof Error ? e.message : String(e));
              })
              .finally(() => { if (targetRead === targetRequest.current) setTargetsLoading(false); });
          })
          .catch((ge) => {
            if (!isLatestRequest(request, recommendationRequest.current)) return;
            setError(
              // 404 here = the quarter has no confirmed violations to rank yet
              ge instanceof ApiError && ge.status === 404
                ? noData
                : ge instanceof Error
                  ? ge.message
                  : String(ge),
            );
          });
      })
      .finally(() => {
        if (isLatestRequest(request, recommendationRequest.current)) setLoading(false);
      });
    return () => { ++epoch.current; };
  }, [quarter]);

  return (
    <div className={styles.page}>
      <p className={styles.desc}>
        분기별 확정 위반 통계를 바탕으로 우선 교육이 필요한 항목을 추천합니다.
      </p>

      {error && (
        <p style={{ color: '#e53e3e', marginBottom: '1rem' }}>⚠ {error}</p>
      )}
      {loading && (
        <p style={{ color: 'var(--text-muted)', marginBottom: '1rem' }}>
          데이터를 불러오는 중...
        </p>
      )}

      {latest && (
        <section className={styles.notice}>
          <strong>최신 추천 목록을 확인하세요</strong>
          <p>
            이전 대상에 대한 초안은 보존되어 있습니다. 아래에서 현재 편집할 추천 대상을
            명시적으로 다시 선택하면 최신 목록으로 전환합니다.
          </p>
          <div className={styles.cardList}>
            {latest.items.length === 0 && (
              <button type="button" className={styles.reselect} onClick={() => selectLatest(latest)}>
                추천이 없는 최신 목록으로 전환
              </button>
            )}
            {latest.items.map((item) => (
              <button
                key={item.recommendation_id}
                type="button"
                className={styles.reselect}
                onClick={() => selectLatest(latest, item.recommendation_id)}
              >
                {item.zone_name} · {item.ppe_type} · {item.education_topic} 선택
              </button>
            ))}
          </div>
        </section>
      )}

      <section className={styles.card} aria-label="통합 교육 대상 선택">
        <strong>통합 교육 대상 선택 · 같은 PPE의 서로 다른 2~20개 구역</strong>
        <p className={styles.desc}>상위 3개 추천과 별도로 전체 저장 대상에서 선택합니다. 이전에 생성된 추천은 일부 구역만 포함할 수 있습니다. 전체 대상을 확보하려면 추천을 명시적으로 갱신하세요.</p>
        <div>
          <button type="button" className={styles.reselect} disabled={targetsLoading || loading} onClick={() => void readTargets(true)}>추천·전체 대상 갱신</button>
          <button type="button" className={styles.reselect} disabled={targetsLoading || loading} onClick={() => void readTargets(false)}>저장된 대상 다시 조회</button>
        </div>
        {targetsLoading && <p>통합 대상을 불러오는 중...</p>}
        {targetsError && <p role="alert">통합 대상 조회·갱신 실패: {targetsError}. 기존 선택과 입력은 유지됩니다. 다시 조회하거나 명시적으로 갱신하세요.</p>}
        {pendingTargets && <div className={styles.notice}>
          <p>최신 통합 대상 {pendingTargets.items.length}개를 조회했습니다. 적용하면 구역을 다시 선택하고 미리보기를 확인해야 합니다. 작성 중인 내용은 보존됩니다.</p>
          <button type="button" className={styles.reselect} disabled={targetsLoading} onClick={() => {
            setTargets(pendingTargets);
            setPendingTargets(null);
            setSelection(current => ({ ids: [], revision: current.revision + 1 }));
            if (pendingRecommendations) setData(pendingRecommendations);
            setPendingRecommendations(null);
          }}>최신 대상 적용·구역 다시 선택</button>
        </div>}
        {targets && <p className={styles.desc}>저장된 대상 {targets.items.length}개 · {targets.generated_at ?? '생성 기록 없음'}</p>}
        {targets?.items.length === 0 && <p>저장된 통합 대상이 없습니다. 필요한 경우 추천·전체 대상 갱신을 실행하세요.</p>}
        {targets?.items.map(item => <label key={item.recommendation_id} className={styles.selectRow}>
          <input type="checkbox" checked={selectedIds.includes(item.recommendation_id)} disabled={
            loading || targetsLoading || Boolean(pendingTargets) || (!selectedIds.includes(item.recommendation_id) &&
              (selectedIds.length >= 20 || Boolean(selectedPpe && item.ppe_type !== selectedPpe) || selectedZones.has(item.zone_name)))
          } onChange={event => toggleSelection(item.recommendation_id, event.target.checked)} />
          <span>{item.zone_name} · {PPE_LABEL[item.ppe_type] ?? item.ppe_type} · {item.education_topic} · {item.priority_score.toFixed(1)}점</span>
        </label>)}
      </section>

      {(data || targets) && (
        <EducationBundleEditor
          quarter={quarter}
          recommendationIds={selectedIds}
          selectionRevision={selection.revision}
        />
      )}

      {data && (
        <div className={styles.cardList}>
          {data.items.map((item) => (
              <RecommendCard
                key={item.recommendation_id}
                item={item}
                quarter={data.quarter}
                draft={drafts[item.recommendation_id]}
                onDraftChange={(draft) => storeDraft(item.recommendation_id, draft)}
                onScopeConflict={(scope) => void refreshRecommendationsAfterConflict(scope)}
              />
          ))}
        </div>
      )}

      {data &&
        Object.entries(drafts)
          .filter(
            ([recommendationId]) =>
              !data.items.some((item) => item.recommendation_id === recommendationId),
          )
          .map(([recommendationId, draft]) => (
            <div key={recommendationId} className={styles.card}>
              <p className={styles.notice}>
                이전 추천 대상({recommendationId})의 입력 초안을 보존했습니다. 최신 목록으로
                전환했지만 초안을 다시 확인하거나 복사할 수 있습니다.
              </p>
              <EducationMaterialEditor
                recommendationId={recommendationId}
                scope={draft.editingScope}
                draft={draft}
                onDraftChange={(next) => storeDraft(recommendationId, next)}
                onScopeConflict={(scope) => void refreshRecommendationsAfterConflict(scope)}
              />
            </div>
          ))}
    </div>
  );
}
