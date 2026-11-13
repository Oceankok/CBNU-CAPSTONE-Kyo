import type { FilterState, EventStatus, PpeType } from '../types';
import styles from './FilterBar.module.css';

interface FilterBarProps {
  filters: FilterState;
  // Receives an updater so quick successive changes are applied to the newest filters
  onChange: (change: (current: FilterState) => FilterState) => void;
  zones: string[];
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
  { value: 'unreviewable', label: '검토 불가' },
];

export default function FilterBar({
  filters,
  onChange,
  zones,
}: FilterBarProps) {
  const update = (patch: Partial<FilterState>) =>
    onChange((current) => ({ ...current, ...patch }));

  const advancedActive =
    !!filters.dateFrom ||
    !!filters.dateTo ||
    filters.minConfidence > 0 ||
    filters.zones.length > 0;

  return (
    <div className={styles.filterBar}>
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

      {/* Less-used filters are collapsed; the summary flags when any is active */}
      <details className={styles.advanced}>
        <summary className={styles.advancedToggle}>
          상세 필터{advancedActive ? ' (적용 중)' : ''}
        </summary>
        <div className={styles.advancedBody}>
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
            {filters.minConfidence > 0 && (
              <span className={styles.sliderNote}>
                '검토 필요' 이벤트는 신뢰도가 없어 제외되지 않습니다
              </span>
            )}
          </div>

          {/* Several zones can be combined; the server returns events from any of them */}
          <div className={styles.zoneGroup}>
            {zones.map((zone) => (
              <label key={zone} className={styles.zoneLabel}>
                <input
                  type="checkbox"
                  checked={filters.zones.includes(zone)}
                  onChange={() =>
                    onChange((current) => ({
                      ...current,
                      zones: current.zones.includes(zone)
                        ? current.zones.filter((z) => z !== zone)
                        : [...current.zones, zone],
                    }))
                  }
                />
                {zone}
              </label>
            ))}
          </div>
        </div>
      </details>
    </div>
  );
}
