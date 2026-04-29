import styles from './SummaryCard.module.css';

interface SummaryCardProps {
  label: string;
  value: string | number;
  sub?: string;
  // positive trend shows green arrow, negative shows red
  trend?: number;
}

export default function SummaryCard({
  label,
  value,
  sub,
  trend,
}: SummaryCardProps) {
  const showTrend = trend !== undefined && trend !== 0;
  const isUp = trend !== undefined && trend > 0;

  return (
    <div className={styles.card}>
      <span className={styles.label}>{label}</span>
      <span className={styles.value}>{value}</span>
      <div className={styles.bottom}>
        {sub && <span className={styles.sub}>{sub}</span>}
        {showTrend && (
          <span className={`${styles.trend} ${isUp ? styles.up : styles.down}`}>
            {isUp ? '▲' : '▼'} {Math.abs(trend!)}%
          </span>
        )}
      </div>
    </div>
  );
}
