import type { FilterState, EventStatus, PpeType } from '../types';
import { AVAILABLE_ZONES } from '../mock';
import styles from './FilterBar.module.css';

interface FilterBarProps {
  filters: FilterState;
  onChange: (filters: FilterState) => void;
}

const PPE_OPTIONS: { value: PpeType; label: string }[] = [
  { value: 'all', label: '전체 PPE' },
  { value: 'helmet', label: '안전모' },
  { value: 'vest', label: '안전조끼' },
];

const STATUS_OPTIONS: { value: EventStatus | 'all'; label: string }[] = [
  { value: 'all', label: '전체 상태' },
  { value: 'pending', label: '미검토' },
  { value: 'confirmed', label: '확정 위반' },
  { value: 'false_positive', label: '오탐' },
  { value: 'hold', label: '보류' },
];

export default function FilterBar({ filters, onChange }: FilterBarProps) {
  const update = (patch: Partial<FilterState>) =>
    onChange({ ...filters, ...patch });

  const toggleZone = (zone: string) => {
    const next = filters.zone.includes(zone)
      ? filters.zone.filter((z) => z !== zone)
      : [...filters.zone, zone];
    update({ zone: next });
  };

  return (
    <div className={styles.filterBar}>
      {/* PPE type filter */}
      <select
        className={styles.select}
        value={filters.ppeType}
        onChange={(e) => update({ ppeType: e.target.value as PpeType })}
      >
        {PPE_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>

      {/* Status filter */}
      <select
        className={styles.select}
        value={filters.status}
        onChange={(e) =>
          update({ status: e.target.value as EventStatus | 'all' })
        }
      >
        {STATUS_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>

      {/* Date range */}
      <input
        type="date"
        className={styles.dateInput}
        value={filters.dateFrom}
        onChange={(e) => update({ dateFrom: e.target.value })}
      />
      <span className={styles.dateSep}>~</span>
      <input
        type="date"
        className={styles.dateInput}
        value={filters.dateTo}
        onChange={(e) => update({ dateTo: e.target.value })}
      />

      {/* Confidence threshold slider */}
      <div className={styles.sliderWrapper}>
        <label className={styles.sliderLabel}>
          신뢰도 ≥ {filters.minConfidence.toFixed(1)}
        </label>
        <input
          type="range"
          min={0}
          max={1}
          step={0.1}
          value={filters.minConfidence}
          onChange={(e) =>
            update({ minConfidence: parseFloat(e.target.value) })
          }
          className={styles.slider}
        />
      </div>

      {/* Zone multi-select as checkboxes */}
      <div className={styles.zoneGroup}>
        {AVAILABLE_ZONES.map((zone) => (
          <label key={zone} className={styles.zoneLabel}>
            <input
              type="checkbox"
              checked={filters.zone.includes(zone)}
              onChange={() => toggleZone(zone)}
            />
            {zone}
          </label>
        ))}
      </div>
    </div>
  );
}
