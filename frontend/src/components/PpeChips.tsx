import { ZONE_PPE, ppeInfo } from '../ppe';
import type { ZonePpe } from '../types';
import styles from './PpeChips.module.css';

// Read-only chips, or all 8 PPE codes as toggles when onChange is given
export default function PpeChips({
  value,
  onChange,
  emptyText = '추가 PPE 없음',
}: {
  value: ZonePpe[];
  onChange?: (next: ZonePpe[]) => void;
  emptyText?: string;
}) {
  if (!onChange) {
    return value.length ? (
      <span className={styles.chips}>
        {value.map((p) => (
          <span key={p} className={styles.chip}>
            {ppeInfo(p).icon} {ppeInfo(p).label}
          </span>
        ))}
      </span>
    ) : (
      <span className={styles.empty}>{emptyText}</span>
    );
  }
  return (
    <div className={styles.chips} role="group" aria-label="추가 PPE">
      {ZONE_PPE.map((o) => {
        const on = value.includes(o.value);
        return (
          <button
            key={o.value}
            type="button"
            className={`${styles.toggle} ${on ? styles.toggleOn : ''}`}
            aria-pressed={on}
            onClick={() =>
              onChange(
                on ? value.filter((x) => x !== o.value) : [...value, o.value],
              )
            }
          >
            {o.icon} {o.label}
          </button>
        );
      })}
    </div>
  );
}
