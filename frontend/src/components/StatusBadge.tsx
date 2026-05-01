import type { EventStatus } from '../types';
import styles from './StatusBadge.module.css';

const STATUS_CONFIG: Record<EventStatus, { label: string; className: string }> =
  {
    pending: { label: '미검토', className: styles.pending },
    confirmed: { label: '확정 위반', className: styles.confirmed },
    false_positive: { label: '오탐', className: styles.falsePositive },
    hold: { label: '보류', className: styles.hold },
  };

interface StatusBadgeProps {
  status: EventStatus;
}

export default function StatusBadge({ status }: StatusBadgeProps) {
  const { label, className } = STATUS_CONFIG[status];
  return <span className={`${styles.badge} ${className}`}>{label}</span>;
}
