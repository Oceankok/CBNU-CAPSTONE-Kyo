import { MOCK_RECOMMENDATIONS } from '../mock';
import type { EducationRecommendation } from '../types';
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
          <span className={styles.tag}>{PPE_LABEL[item.ppe_type] ?? item.ppe_type}</span>
          <span className={styles.tag}>{item.zone_name}</span>
        </div>
        <span className={styles.score}>{item.priority_score.toFixed(1)}점</span>
      </div>

      <p className={styles.topic}>{item.education_topic}</p>

      <div className={styles.breakdown}>
        <p className={styles.breakdownTitle}>점수 산정 근거</p>
        <ScoreRow label="확정 위반 건수" value={`${item.score_breakdown.confirmed_count}건`} />
        <ScoreRow label="반복 발생 주수" value={`${item.score_breakdown.repeat_weeks}주`} />
        <ScoreRow
          label="구역 집중도"
          value={(item.score_breakdown.zone_concentration * 100).toFixed(0) + '%'}
        />
        <ScoreRow
          label="공정 위험 가중치"
          value={item.score_breakdown.process_risk_weight.toFixed(1)}
        />
      </div>
    </div>
  );
}

export default function RecommendPage() {
  const { quarter, items } = MOCK_RECOMMENDATIONS;

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <h2 className={styles.title}>교육 추천</h2>
        <span className={styles.quarterBadge}>{quarter}</span>
      </div>

      <p className={styles.desc}>
        분기별 확정 위반 통계를 바탕으로 우선 교육이 필요한 항목을 추천합니다.
      </p>

      <div className={styles.cardList}>
        {items.map((item) => (
          <RecommendCard key={item.recommendation_id} item={item} />
        ))}
      </div>
    </div>
  );
}

