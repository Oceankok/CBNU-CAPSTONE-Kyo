import { useState, useEffect } from 'react';
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
import EducationMaterialEditor from '../components/EducationMaterialEditor';
import type { EducationMaterialDraft } from '../components/EducationMaterialEditor';
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

  const storeDraft = (recommendationId: string, draft: EducationMaterialDraft | null) => {
    setDrafts((current) => {
      const next = { ...current };
      if (draft) next[recommendationId] = draft;
      else delete next[recommendationId];
      return next;
    });
  };

  const refreshRecommendationsAfterConflict = async (scope: EducationMaterialScope) => {
    try {
      setLatest(await fetchRecommendations(scope.quarter));
      setError(
        '추천 목록이 변경되었습니다. 입력 중인 초안은 보존했습니다. 최신 추천 대상을 명시적으로 다시 선택하세요.',
      );
    } catch (e) {
      setError(
        `최신 추천 목록을 불러오지 못했습니다. 기존 초안은 보존되어 있습니다. (${e instanceof Error ? e.message : String(e)})`,
      );
    }
  };

  const selectLatest = (next: EducationRecommendationList, recommendationId: string) => {
    const selected = next.items.find((item) => item.recommendation_id === recommendationId);
    setData(
      selected
        ? { ...next, items: [selected, ...next.items.filter((item) => item !== selected)] }
        : next,
    );
    setLatest(null);
    setError(null);
  };

  // AppLayout remounts this page when the quarter changes, so this runs once per quarter
  useEffect(() => {
    const noData = `${quarter}에는 추천을 만들 확정 위반 기록이 아직 없습니다.`;
    fetchRecommendations(quarter)
      .then(setData)
      .catch((e) => {
        // Only "no recommendations yet" (404) triggers generation. An expired login, a
        // permission or server error, or a network failure must not start a write (#240)
        if (!(e instanceof ApiError && e.status === 404)) {
          setError(e instanceof Error ? e.message : String(e));
          return;
        }
        return generateRecommendations(quarter)
          .then(setData)
          .catch((ge) =>
            setError(
              // 404 here = the quarter has no confirmed violations to rank yet
              ge instanceof ApiError && ge.status === 404
                ? noData
                : ge instanceof Error
                  ? ge.message
                  : String(ge),
            ),
          );
      })
      .finally(() => setLoading(false));
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
