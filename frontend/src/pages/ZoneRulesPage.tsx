import { useEffect, useState } from 'react';
import { fetchZones, saveZone, deleteZone } from '../api/zones';
import type { ZonePpe, ZoneRule } from '../types';
import { ZONE_PPE } from '../ppe';
import styles from './ZoneRulesPage.module.css';

// Editable copy of a rule; rules are edited as one-per-line text
interface Draft {
  zone_name: string;
  required_ppe: ZonePpe[];
  rulesText: string;
  saved: boolean; // false while there are unsaved edits
  isNew: boolean; // added locally, not on the server yet
}

const toDraft = (z: ZoneRule): Draft => ({
  zone_name: z.zone_name,
  required_ppe: z.required_ppe,
  rulesText: z.rules.join('\n'),
  saved: true,
  isNew: false,
});

const toRule = (d: Draft): ZoneRule => ({
  zone_name: d.zone_name,
  required_ppe: d.required_ppe,
  // Backend rejects duplicate lines (422), so drop repeats here
  rules: [
    ...new Set(
      d.rulesText
        .split('\n')
        .map((r) => r.trim())
        .filter(Boolean),
    ),
  ],
});

export default function ZoneRulesPage() {
  const [drafts, setDrafts] = useState<Draft[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // zone currently saving/deleting
  const [newZone, setNewZone] = useState('');

  useEffect(() => {
    fetchZones()
      .then((zones) => setDrafts(zones.map(toDraft)))
      .catch((e: Error) => setError(e.message));
  }, []);

  const patch = (zone: string, p: Partial<Draft>) =>
    setDrafts(
      (prev) =>
        prev?.map((d) =>
          d.zone_name === zone ? { ...d, ...p, saved: false } : d,
        ) ?? prev,
    );

  const togglePpe = (d: Draft, ppe: ZonePpe) =>
    patch(d.zone_name, {
      required_ppe: d.required_ppe.includes(ppe)
        ? d.required_ppe.filter((p) => p !== ppe)
        : [...d.required_ppe, ppe],
    });

  const handleSave = (d: Draft) => {
    setBusy(d.zone_name);
    setError(null);
    saveZone(toRule(d))
      .then((saved) =>
        setDrafts(
          (prev) =>
            prev?.map((x) =>
              x.zone_name === d.zone_name ? toDraft(saved) : x,
            ) ?? prev,
        ),
      )
      .catch((e: Error) => setError(`${d.zone_name} 저장 실패: ${e.message}`))
      .finally(() => setBusy(null));
  };

  const handleDelete = (d: Draft) => {
    if (!window.confirm(`'${d.zone_name}' 규칙을 삭제할까요?`)) return;
    const removeLocal = () =>
      setDrafts(
        (prev) => prev?.filter((x) => x.zone_name !== d.zone_name) ?? prev,
      );
    // A zone that was never saved only exists locally
    if (d.isNew) return removeLocal();
    setBusy(d.zone_name);
    setError(null);
    deleteZone(d.zone_name)
      .then(removeLocal)
      .catch((e: Error) => setError(`${d.zone_name} 삭제 실패: ${e.message}`))
      .finally(() => setBusy(null));
  };

  const handleAdd = (e: React.FormEvent) => {
    e.preventDefault();
    const name = newZone.trim();
    if (!name || !drafts) return;
    if (drafts.some((d) => d.zone_name === name)) {
      setError(`'${name}' 구역은 이미 있습니다.`);
      return;
    }
    setDrafts([
      ...drafts,
      {
        zone_name: name,
        required_ppe: [],
        rulesText: '',
        saved: false,
        isNew: true,
      },
    ]);
    setNewZone('');
    setError(null);
  };

  return (
    <div className={styles.page}>
      <p className={styles.desc}>
        구역마다 필수 PPE와 작업자에게 보여줄 안전 수칙을 설정합니다.
      </p>

      {error && <p className={styles.errorMsg}>⚠ {error}</p>}
      {!drafts && !error && <p className={styles.desc}>불러오는 중...</p>}

      {drafts?.map((d) => (
        <section key={d.zone_name} className={styles.card}>
          <div className={styles.cardHeader}>
            <h3 className={styles.zoneName}>{d.zone_name}</h3>
            {!d.saved && <span className={styles.unsaved}>저장 안 됨</span>}
          </div>

          <div className={styles.ppeRow} role="group" aria-label="필수 PPE">
            {ZONE_PPE.map((o) => (
              <button
                key={o.value}
                type="button"
                className={`${styles.ppeChip} ${d.required_ppe.includes(o.value) ? styles.ppeOn : ''}`}
                aria-pressed={d.required_ppe.includes(o.value)}
                onClick={() => togglePpe(d, o.value)}
              >
                {o.icon} {o.label}
                {!o.detected && <span className={styles.notDetected}>*</span>}
              </button>
            ))}
          </div>
          <p className={styles.ppeNote}>
            * 현재 AI는 안전모·안전조끼만 자동 탐지합니다. 나머지는 작업자
            안내용입니다.
          </p>

          <label className={styles.field}>
            <span className={styles.fieldLabel}>안전 수칙 (한 줄에 하나)</span>
            <textarea
              className={styles.textarea}
              rows={Math.max(3, d.rulesText.split('\n').length + 1)}
              value={d.rulesText}
              onChange={(e) =>
                patch(d.zone_name, { rulesText: e.target.value })
              }
              placeholder="예: 금형 교체 시 반드시 전원 차단"
            />
          </label>

          <div className={styles.actions}>
            <button
              className={styles.deleteBtn}
              onClick={() => handleDelete(d)}
              disabled={busy === d.zone_name}
            >
              삭제
            </button>
            <button
              className={styles.saveBtn}
              onClick={() => handleSave(d)}
              disabled={d.saved || busy === d.zone_name}
            >
              {busy === d.zone_name ? '저장 중…' : '저장'}
            </button>
          </div>
        </section>
      ))}

      {drafts && (
        <form className={styles.addRow} onSubmit={handleAdd}>
          <input
            className={styles.addInput}
            value={newZone}
            onChange={(e) => setNewZone(e.target.value)}
            placeholder="새 구역 이름"
          />
          <button
            className={styles.addBtn}
            type="submit"
            disabled={!newZone.trim()}
          >
            + 구역 추가
          </button>
        </form>
      )}
    </div>
  );
}
