import { useEffect, useState } from 'react';
import { fetchZones, saveZone, deleteZone } from '../api/zones';
import { fetchUsers, setZoneMembers } from '../api/users';
import { fetchCameras } from '../api/cameras';
import type { Camera } from '../api/cameras';
import type { AppUser, ZonePpe, ZoneRule } from '../types';
import { ZONE_PPE } from '../ppe';
import ConfirmButton from '../components/ConfirmButton';
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
  const [workers, setWorkers] = useState<AppUser[]>([]);
  const [cameras, setCameras] = useState<Camera[]>([]);

  useEffect(() => {
    fetchZones()
      .then((zones) => setDrafts(zones.map(toDraft)))
      .catch((e: Error) => setError(e.message));
    // Members and cameras are context for each zone; the rule editor works without them
    fetchUsers()
      .then((users) => setWorkers(users.filter((u) => u.role === 'worker')))
      .catch(() => {});
    fetchCameras()
      .then(setCameras)
      .catch(() => {});
  }, []);

  // Replaces this zone's worker list only; the workers' other zones stay (#127)
  const assign = (zone: string, userIds: string[]) => {
    setError(null);
    setZoneMembers(zone, userIds)
      .then(() =>
        setWorkers((prev) =>
          prev.map((w) => {
            const others = w.zone_names.filter((z) => z !== zone);
            const names = userIds.includes(w.user_id)
              ? [...others, zone].sort()
              : others;
            return { ...w, zone_names: names, zone_name: names[0] ?? null };
          }),
        ),
      )
      .catch((e: Error) => setError(`${zone} 작업자 변경 실패: ${e.message}`));
  };

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

          <ZoneMembers
            zone={d.zone_name}
            saved={!d.isNew}
            workers={workers}
            cameras={cameras.filter((c) => c.zone_name === d.zone_name)}
            onAssign={assign}
          />

          <div className={styles.actions}>
            <ConfirmButton
              className={styles.deleteBtn}
              label="삭제"
              question={`'${d.zone_name}' 규칙을 삭제할까요?`}
              confirmLabel="삭제"
              onConfirm={() => handleDelete(d)}
              disabled={busy === d.zone_name}
            />
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

// Workers assigned to the zone (add / remove) and the cameras installed there
function ZoneMembers({
  zone,
  saved,
  workers,
  cameras,
  onAssign,
}: {
  zone: string;
  saved: boolean;
  workers: AppUser[];
  cameras: Camera[];
  onAssign: (zone: string, userIds: string[]) => void;
}) {
  const members = workers.filter((w) => w.zone_names.includes(zone));
  const others = workers.filter((w) => !w.zone_names.includes(zone));

  return (
    <div className={styles.members}>
      <div className={styles.memberBlock}>
        <span className={styles.fieldLabel}>작업자 {members.length}명</span>
        {!saved ? (
          <p className={styles.ppeNote}>
            구역을 저장한 뒤 작업자를 추가할 수 있습니다.
          </p>
        ) : (
          <>
            <div className={styles.memberList}>
              {members.length === 0 && (
                <span className={styles.ppeNote}>
                  배정된 작업자가 없습니다.
                </span>
              )}
              {members.map((w) => (
                <span
                  key={w.user_id}
                  className={`${styles.member} ${w.is_active ? '' : styles.memberOff}`}
                >
                  {w.display_name}
                  {!w.is_active && ' (정지)'}
                  <button
                    type="button"
                    className={styles.memberRemove}
                    aria-label={`${w.display_name} 구역에서 제외`}
                    onClick={() =>
                      onAssign(
                        zone,
                        members
                          .filter((m) => m.user_id !== w.user_id)
                          .map((m) => m.user_id),
                      )
                    }
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
            {others.length > 0 && (
              <select
                className={styles.memberAdd}
                value=""
                onChange={(e) => {
                  const w = others.find((x) => x.user_id === e.target.value);
                  if (w)
                    onAssign(
                      zone,
                      [...members, w].map((m) => m.user_id),
                    );
                }}
                aria-label={`${zone}에 작업자 추가`}
              >
                <option value="">+ 작업자 추가</option>
                {others.map((w) => (
                  <option key={w.user_id} value={w.user_id}>
                    {w.display_name} ({w.user_id})
                    {w.zone_names.length ? ` — ${w.zone_names.join(', ')}` : ''}
                  </option>
                ))}
              </select>
            )}
          </>
        )}
      </div>
      <div className={styles.memberBlock}>
        <span className={styles.fieldLabel}>카메라 {cameras.length}대</span>
        <span className={styles.ppeNote}>
          {cameras.length
            ? cameras
                .map(
                  (c) =>
                    c.camera_id +
                    (c.camera_location ? ` (${c.camera_location})` : ''),
                )
                .join(', ')
            : '설치된 카메라가 없습니다.'}
        </span>
      </div>
    </div>
  );
}
