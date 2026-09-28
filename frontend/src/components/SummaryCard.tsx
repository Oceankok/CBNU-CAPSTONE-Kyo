import styles from './SummaryCard.module.css';

interface SummaryCardProps {
  label: string;
  value: string | number;
  sub?: string;
  // positive trend shows green arrow, negative shows red
  trend?: number;
  // highlight the key metric (red value + border)
  accent?: boolean;
}

export default function SummaryCard({
  label,
  value,
  sub,
  trend,
  accent,
}: SummaryCardProps) {
  const showTrend = trend !== undefined && trend !== 0;
  const isUp = trend !== undefined && trend > 0;

  return (
    <div className={`${styles.card} ${accent ? styles.accent : ''}`}>
      <span className={styles.label}>{label}</span>
      <span className={styles.value}>{value}</span>
      {(sub || showTrend) && (
        <div className={styles.bottom}>
          {sub && <span className={styles.sub}>{sub}</span>}
          {showTrend && (
            <span
              className={`${styles.trend} ${isUp ? styles.up : styles.down}`}
            >
              {isUp ? '▲' : '▼'} {Math.abs(trend!)}%
            </span>
          )}
        </div>
      )}
    </div>
  );
}
