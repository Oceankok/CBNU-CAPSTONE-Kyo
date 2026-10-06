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

function RecommendCard({ item }: { item: EducationRecommendation }) {
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
      </details>
    </div>
  );
}

export default function RecommendPage() {
  const { quarter } = useOutletContext<LayoutContext>();
  const [data, setData] = useState<EducationRecommendationList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // AppLayout remounts this page when the quarter changes, so this runs once per quarter
  useEffect(() => {
    fetchRecommendations(quarter)
      .then(setData)
      .catch(() =>
        // If no recommendations exist yet, auto-generate for the current quarter
        generateRecommendations(quarter)
          .then(setData)
          .catch((e) =>
            setError(
              // 4xx here means the quarter has no confirmed violations to rank yet
              e instanceof ApiError && e.status < 500
                ? `${quarter}에는 추천을 만들 확정 위반 기록이 아직 없습니다.`
                : e.message,
            ),
          ),
      )
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
        <p style={{ color: '#718096', marginBottom: '1rem' }}>
          데이터를 불러오는 중...
        </p>
      )}

      {data && (
        <div className={styles.cardList}>
          {data.items.map((item) => (
            <RecommendCard key={item.recommendation_id} item={item} />
          ))}
        </div>
      )}
    </div>
  );
}
