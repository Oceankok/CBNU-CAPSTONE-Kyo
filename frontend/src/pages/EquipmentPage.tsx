import { useCallback, useEffect, useState } from 'react';
import {
  fetchEquipment,
  createEquipment,
  updateEquipment,
  fetchCameras,
  fetchCameraEquipment,
  saveCameraEquipment,
  fetchPpePolicy,
} from '../api/equipment';
import type {
  Camera,
  Equipment,
  EquipmentInput,
  PpePolicy,
} from '../api/equipment';
import { fetchZones } from '../api/zones';
import { ZONE_PPE, ppeInfo } from '../ppe';
import type { ZonePpe } from '../types';
import styles from './EquipmentPage.module.css';

// Business error codes from the equipment contract (#112)
const ERRORS: Record<string, string> = {
  equipment_not_found: '장비를 찾을 수 없습니다.',
  camera_not_found: '카메라를 찾을 수 없습니다.',
  zone_not_found: '등록되지 않은 구역입니다. 구역 규칙에서 먼저 만드세요.',
  equipment_has_camera_links:
    '카메라에 연결된 장비는 구역을 옮길 수 없습니다. 카메라 연결을 먼저 해제하세요.',
  equipment_zone_mismatch: '카메라와 같은 구역의 장비만 연결할 수 있습니다.',
  equipment_inactive: '비활성 장비는 연결할 수 없습니다.',
};
const msg = (e: unknown) => {
  const m = e instanceof Error ? e.message : String(e);
  return ERRORS[m] ?? m;
};
const ISSUE_LABEL: Record<string, string> = {
  camera_zone_unassigned: '카메라에 구역이 지정되지 않음',
  zone_rule_missing: '구역 규칙이 없음',
  equipment_scope_unconfigured: '이 카메라의 장비 연결을 아직 설정하지 않음',
};

const EMPTY: EquipmentInput = {
  name: '',
  equipment_type: null,
  zone_name: '',
  required_ppe: [],
};

function PpeChips({
  value,
  onToggle,
}: {
  value: ZonePpe[];
  onToggle?: (p: ZonePpe) => void;
}) {
  if (!onToggle) {
    return value.length ? (
      <span className={styles.chips}>
        {value.map((p) => (
          <span key={p} className={styles.chip}>
            {ppeInfo(p).icon} {ppeInfo(p).label}
          </span>
        ))}
      </span>
    ) : (
      <span className={styles.muted}>추가 PPE 없음</span>
    );
  }
  return (
    <div className={styles.chips} role="group" aria-label="추가 PPE">
      {ZONE_PPE.map((o) => (
        <button
          key={o.value}
          type="button"
          className={`${styles.toggle} ${value.includes(o.value) ? styles.toggleOn : ''}`}
          aria-pressed={value.includes(o.value)}
          onClick={() => onToggle(o.value)}
        >
          {o.icon} {o.label}
        </button>
      ))}
    </div>
  );
}

const toggled = (list: ZonePpe[], p: ZonePpe) =>
  list.includes(p) ? list.filter((x) => x !== p) : [...list, p];

export default function EquipmentPage() {
  const [equipment, setEquipment] = useState<Equipment[] | null>(null);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [zones, setZones] = useState<string[]>([]);
  const [zoneFilter, setZoneFilter] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null); // equipment_id, or 'new'
  const [draft, setDraft] = useState<EquipmentInput>(EMPTY);
  const [busy, setBusy] = useState(false);

  const reloadEquipment = useCallback(
    () => fetchEquipment().then(setEquipment),
    [],
  );

  useEffect(() => {
    Promise.all([fetchEquipment(), fetchCameras(), fetchZones()])
      .then(([eq, cams, zs]) => {
        setEquipment(eq);
        setCameras(cams);
        setZones(zs.map((z) => z.zone_name));
      })
      .catch((e) => setError(msg(e)));
  }, []);

  const startEdit = (e: Equipment | null) => {
    setError(null);
    setEditing(e ? e.equipment_id : 'new');
    setDraft(
      e
        ? {
            name: e.name,
            equipment_type: e.equipment_type,
            zone_name: e.zone_name,
            required_ppe: e.required_ppe,
          }
        : { ...EMPTY, zone_name: zoneFilter || zones[0] || '' },
    );
  };

  const run = async (op: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await op();
      await reloadEquipment();
      return true;
    } catch (e) {
      setError(msg(e));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    const body = {
      ...draft,
      name: draft.name.trim(),
      equipment_type: draft.equipment_type?.trim() || null,
    };
    const ok = await run(() =>
      editing === 'new'
        ? createEquipment(body)
        : updateEquipment(editing!, body),
    );
    if (ok) setEditing(null);
  };

  const setStatus = (e: Equipment, status: Equipment['status']) => {
    if (
      status === 'active' &&
      !window.confirm(
        `'${e.name}'을(를) 다시 활성화하면 기존 카메라 연결에도 추가 PPE가 다시 적용됩니다. 계속할까요?`,
      )
    )
      return;
    void run(() => updateEquipment(e.equipment_id, { status }));
  };

  const visible = (equipment ?? []).filter(
    (e) => !zoneFilter || e.zone_name === zoneFilter,
  );
  const visibleCameras = cameras.filter(
    (c) => !zoneFilter || c.zone_name === zoneFilter,
  );

  const form = (
    <div className={styles.form}>
      <div className={styles.formRow}>
        <input
          className={styles.input}
          placeholder="장비 이름 (예: 프레스 1호기)"
          value={draft.name}
          maxLength={100}
          onChange={(e) => setDraft({ ...draft, name: e.target.value })}
        />
        <input
          className={styles.input}
          placeholder="종류 (선택, 예: press)"
          value={draft.equipment_type ?? ''}
          maxLength={100}
          onChange={(e) =>
            setDraft({ ...draft, equipment_type: e.target.value })
          }
        />
        <select
          className={styles.input}
          value={draft.zone_name}
          onChange={(e) => setDraft({ ...draft, zone_name: e.target.value })}
        >
          <option value="">구역 선택</option>
          {zones.map((z) => (
            <option key={z} value={z}>
              {z}
            </option>
          ))}
        </select>
      </div>
      <p className={styles.label}>
        구역 기본 PPE에 더해 이 장비 앞에서 필요한 PPE
      </p>
      <PpeChips
        value={draft.required_ppe}
        onToggle={(p) =>
          setDraft({ ...draft, required_ppe: toggled(draft.required_ppe, p) })
        }
      />
      <div className={styles.actions}>
        <button className={styles.btn} onClick={() => setEditing(null)}>
          취소
        </button>
        <button
          className={styles.primaryBtn}
          disabled={busy || !draft.name.trim() || !draft.zone_name}
          onClick={save}
        >
          {editing === 'new' ? '등록' : '저장'}
        </button>
      </div>
    </div>
  );

  return (
    <div className={styles.page}>
      <p className={styles.desc}>
        장비별로 추가로 필요한 PPE를 등록하고, 카메라에 장비를 연결하면 해당
        카메라의 적용 PPE가 계산됩니다.
      </p>
      {error && <p className={styles.errorMsg}>⚠ {error}</p>}
      {!equipment && !error && <p className={styles.desc}>불러오는 중...</p>}

      {equipment && (
        <>
          <div className={styles.filters} role="tablist" aria-label="구역">
            {['', ...zones].map((z) => (
              <button
                key={z || 'all'}
                role="tab"
                aria-selected={zoneFilter === z}
                className={`${styles.filter} ${zoneFilter === z ? styles.filterOn : ''}`}
                onClick={() => setZoneFilter(z)}
              >
                {z || '전체'}
              </button>
            ))}
          </div>

          <section className={styles.card}>
            <div className={styles.cardHeader}>
              <h3 className={styles.cardTitle}>장비 ({visible.length})</h3>
              {editing !== 'new' && (
                <button className={styles.btn} onClick={() => startEdit(null)}>
                  + 장비 등록
                </button>
              )}
            </div>
            {editing === 'new' && form}
            {visible.length === 0 && editing !== 'new' && (
              <p className={styles.muted}>등록된 장비가 없습니다.</p>
            )}
            <ul className={styles.list}>
              {visible.map((e) => (
                <li
                  key={e.equipment_id}
                  className={`${styles.item} ${e.status === 'inactive' ? styles.inactive : ''}`}
                >
                  {editing === e.equipment_id ? (
                    form
                  ) : (
                    <>
                      <div className={styles.itemMain}>
                        <span className={styles.name}>{e.name}</span>
                        <span className={styles.meta}>
                          {e.zone_name}
                          {e.equipment_type && ` · ${e.equipment_type}`}
                          {e.status === 'inactive' && ' · 비활성'}
                        </span>
                        <PpeChips value={e.required_ppe} />
                      </div>
                      <div className={styles.itemActions}>
                        <button
                          className={styles.linkBtn}
                          onClick={() => startEdit(e)}
                          disabled={busy}
                        >
                          수정
                        </button>
                        <button
                          className={styles.linkBtn}
                          disabled={busy}
                          onClick={() =>
                            setStatus(
                              e,
                              e.status === 'active' ? 'inactive' : 'active',
                            )
                          }
                        >
                          {e.status === 'active' ? '비활성화' : '활성화'}
                        </button>
                      </div>
                    </>
                  )}
                </li>
              ))}
            </ul>
          </section>

          <section className={styles.card}>
            <h3 className={styles.cardTitle}>카메라별 적용 PPE</h3>
            <p className={styles.muted}>
              카메라 화면 전체에 적용할 장비를 연결합니다. 적용 PPE = 구역 기본
              PPE + 연결된 활성 장비의 추가 PPE
            </p>
            {visibleCameras.length === 0 && (
              <p className={styles.muted}>카메라가 없습니다.</p>
            )}
            <ul className={styles.list}>
              {visibleCameras.map((c) => (
                <CameraRow
                  key={c.camera_id}
                  camera={c}
                  equipment={equipment}
                  onError={setError}
                />
              ))}
            </ul>
          </section>
        </>
      )}
    </div>
  );
}

function CameraRow({
  camera,
  equipment,
  onError,
}: {
  camera: Camera;
  equipment: Equipment[];
  onError: (m: string | null) => void;
}) {
  const [linked, setLinked] = useState<string[] | null>(null);
  const [policy, setPolicy] = useState<PpePolicy | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  // Re-read when the equipment list changes (an edit can change the policy)
  useEffect(() => {
    Promise.all([
      fetchCameraEquipment(camera.camera_id),
      fetchPpePolicy(camera.camera_id),
    ])
      .then(([link, p]) => {
        setLinked(link.equipment_ids);
        setPolicy(p);
      })
      .catch((e) => onError(msg(e)));
  }, [camera.camera_id, equipment, onError]);

  const candidates = equipment.filter(
    (e) => e.zone_name === camera.zone_name && e.status === 'active',
  );
  const name = (id: string) => equipment.find((e) => e.equipment_id === id);

  const save = () => {
    setBusy(true);
    onError(null);
    saveCameraEquipment(camera.camera_id, draft)
      .then((link) => {
        setLinked(link.equipment_ids);
        setEditing(false);
        return fetchPpePolicy(camera.camera_id).then(setPolicy);
      })
      .catch((e) => onError(msg(e)))
      .finally(() => setBusy(false));
  };

  return (
    <li className={styles.item}>
      <div className={styles.itemMain}>
        <span className={styles.name}>{camera.camera_id}</span>
        <span className={styles.meta}>
          {camera.zone_name ?? '구역 미지정'}
          {camera.camera_location && ` · ${camera.camera_location}`}
        </span>
        <span className={styles.meta}>
          연결 장비:{' '}
          {linked === null
            ? '…'
            : linked.length
              ? linked
                  .map(
                    (id) =>
                      `${name(id)?.name ?? id}${name(id)?.status === 'inactive' ? '(비활성)' : ''}`,
                  )
                  .join(', ')
              : '없음'}
        </span>
        {policy &&
          (policy.required_ppe ? (
            <div className={styles.policy}>
              <span className={styles.label}>적용 PPE</span>
              <PpeChips value={policy.required_ppe} />
              {policy.unsupported_ppe.length > 0 && (
                <span className={styles.muted}>
                  자동 탐지 미지원:{' '}
                  {policy.unsupported_ppe
                    .map((p) => ppeInfo(p).label)
                    .join(', ')}{' '}
                  (작업자 안내용)
                </span>
              )}
            </div>
          ) : (
            <p className={styles.warn}>
              설정 미완료 —{' '}
              {policy.configuration_issues
                .map((i) => ISSUE_LABEL[i] ?? i)
                .join(', ')}
            </p>
          ))}
        {editing && (
          <div className={styles.form}>
            {candidates.length === 0 ? (
              <p className={styles.muted}>
                이 구역에 연결할 수 있는 활성 장비가 없습니다.
              </p>
            ) : (
              candidates.map((e) => (
                <label key={e.equipment_id} className={styles.check}>
                  <input
                    type="checkbox"
                    checked={draft.includes(e.equipment_id)}
                    onChange={() =>
                      setDraft(
                        draft.includes(e.equipment_id)
                          ? draft.filter((x) => x !== e.equipment_id)
                          : [...draft, e.equipment_id],
                      )
                    }
                  />
                  {e.name}
                </label>
              ))
            )}
            <p className={styles.muted}>
              장비를 하나도 선택하지 않고 저장하면 "연결 장비 없음"으로
              설정됩니다.
            </p>
            <div className={styles.actions}>
              <button className={styles.btn} onClick={() => setEditing(false)}>
                취소
              </button>
              <button
                className={styles.primaryBtn}
                disabled={busy}
                onClick={save}
              >
                저장
              </button>
            </div>
          </div>
        )}
      </div>
      {!editing && camera.zone_name && (
        <div className={styles.itemActions}>
          <button
            className={styles.linkBtn}
            onClick={() => {
              // Inactive links stay listed by the API; drop them from the editable set
              setDraft(
                (linked ?? []).filter((id) => name(id)?.status === 'active'),
              );
              setEditing(true);
            }}
          >
            장비 연결
          </button>
        </div>
      )}
    </li>
  );
}
