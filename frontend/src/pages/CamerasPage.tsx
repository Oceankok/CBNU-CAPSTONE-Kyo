import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { MOCK_AUTH } from '../api/auth';
import { fetchCameras, createCamera, updateCamera } from '../api/cameras';
import type { Camera, CameraFields } from '../api/cameras';
import { fetchZones } from '../api/zones';
import { fetchNodes } from '../api/nodes';
import ConfirmButton from '../components/ConfirmButton';
import CameraGroups from '../components/CameraGroups';
import styles from './CamerasPage.module.css';

// Backend business error codes (#150 / #158)
const ERRORS: Record<string, string> = {
  camera_already_exists: '이미 사용 중인 카메라 ID입니다.',
  camera_not_found: '카메라를 찾을 수 없습니다.',
  zone_not_found: '등록되지 않은 구역입니다. 구역 규칙에서 먼저 만드세요.',
  camera_has_equipment_links:
    '장비가 연결된 카메라는 구역을 바꿀 수 없습니다. 장비 관리에서 연결을 먼저 해제하세요.',
};
const msg = (e: unknown) => {
  const m = e instanceof Error ? e.message : String(e);
  return ERRORS[m] ?? m;
};

const STATUS: Record<Camera['status'], { label: string; cls: string }> = {
  active: { label: '사용 중', cls: 'on' },
  inactive: { label: '중지됨', cls: 'off' },
  unknown: { label: '상태 미지정', cls: 'unknown' },
};

const EMPTY: CameraFields & { camera_id: string } = {
  camera_id: '',
  camera_location: null,
  zone_name: null,
  process_type: null,
  camera_angle_type: null,
};

type Draft = CameraFields;

export default function CamerasPage() {
  const [cameras, setCameras] = useState<Camera[] | null>(null);
  const [zones, setZones] = useState<string[]>([]);
  const [nodeNames, setNodeNames] = useState<Record<string, string>>({});
  const [msgState, setMsgState] = useState<{
    ok: boolean;
    text: string;
  } | null>(null);
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetchCameras()
      .then(setCameras)
      .catch((e) => setMsgState({ ok: false, text: msg(e) }));
    // Context only: names for the linked PCs and the zone choices
    fetchZones()
      .then((z) => setZones(z.map((x) => x.zone_name)))
      .catch(() => {});
    fetchNodes()
      .then((n) =>
        setNodeNames(Object.fromEntries(n.map((x) => [x.node_id, x.name]))),
      )
      .catch(() => {});
  }, []);

  const replace = (c: Camera) =>
    setCameras(
      (prev) => prev?.map((x) => (x.camera_id === c.camera_id ? c : x)) ?? prev,
    );

  const run = async (op: () => Promise<void>) => {
    setBusy(true);
    setMsgState(null);
    try {
      await op();
    } catch (e) {
      setMsgState({ ok: false, text: msg(e) });
    } finally {
      setBusy(false);
    }
  };

  const blank = (v: string | null | undefined) => v?.trim() || null;

  const add = (e: React.FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const created = await createCamera({
        camera_id: form.camera_id.trim(),
        camera_location: blank(form.camera_location),
        zone_name: form.zone_name,
        process_type: blank(form.process_type),
        camera_angle_type: blank(form.camera_angle_type),
      });
      setCameras((prev) =>
        [...(prev ?? []), created].sort((a, b) =>
          a.camera_id.localeCompare(b.camera_id),
        ),
      );
      setForm(EMPTY);
      setAdding(false);
      setMsgState({
        ok: true,
        text: `${created.camera_id} 카메라를 등록했습니다. 현장 PC 연결은 설정 → 현장 PC에서 합니다.`,
      });
    });
  };

  const startEdit = (c: Camera) => {
    setMsgState(null);
    setEditing(c.camera_id);
    setDraft({
      camera_location: c.camera_location,
      zone_name: c.zone_name,
      process_type: c.process_type,
      camera_angle_type: c.camera_angle_type,
    });
  };

  const save = (c: Camera) =>
    void run(async () => {
      replace(
        await updateCamera(c.camera_id, {
          camera_location: blank(draft.camera_location),
          zone_name: draft.zone_name,
          process_type: blank(draft.process_type),
          camera_angle_type: blank(draft.camera_angle_type),
        }),
      );
      setEditing(null);
    });

  const setStatus = (c: Camera, status: 'active' | 'inactive') =>
    void run(async () => {
      replace(await updateCamera(c.camera_id, { status }));
    });

  const zoneSelect = (
    value: string | null,
    onChange: (v: string | null) => void,
  ) => (
    <select
      className={styles.input}
      value={value ?? ''}
      onChange={(e) => onChange(e.target.value || null)}
    >
      <option value="">구역 미지정</option>
      {/* Keep a zone that no longer has rules selectable so the current value still shows */}
      {[...new Set([...zones, ...(value ? [value] : [])])].map((z) => (
        <option key={z} value={z}>
          {z}
        </option>
      ))}
    </select>
  );

  const textInput = (
    value: string | null,
    onChange: (v: string) => void,
    placeholder: string,
    maxLength: number,
  ) => (
    <input
      className={styles.input}
      value={value ?? ''}
      placeholder={placeholder}
      maxLength={maxLength}
      onChange={(e) => onChange(e.target.value)}
    />
  );

  const pcName = (id: string | null) => (id ? (nodeNames[id] ?? id) : '미연결');

  return (
    <div className={styles.page}>
      <p className={styles.desc}>
        이벤트와 영상은 등록된 카메라에서만 받습니다. 중지한 카메라의 새
        이벤트·영상은 거절되고, 기존 이벤트는 그대로 볼 수 있습니다.
      </p>
      {msgState && (
        <p className={msgState.ok ? styles.okMsg : styles.errorMsg}>
          {msgState.ok ? '✓' : '⚠'} {msgState.text}
        </p>
      )}
      {!cameras && !msgState && <p className={styles.desc}>불러오는 중...</p>}

      {cameras && (
        <section className={styles.card}>
          <div className={styles.cardHeader}>
            <h3 className={styles.cardTitle}>카메라 ({cameras.length})</h3>
            {!adding && (
              <button
                className={styles.btn}
                onClick={() => {
                  setMsgState(null);
                  setAdding(true);
                }}
              >
                + 카메라 등록
              </button>
            )}
          </div>

          {adding && (
            <form className={styles.form} onSubmit={add}>
              <div className={styles.formGrid}>
                <input
                  className={styles.input}
                  placeholder="카메라 ID (영문·숫자·-·_)"
                  pattern="[A-Za-z0-9_\-]{1,100}"
                  maxLength={100}
                  value={form.camera_id}
                  autoCapitalize="none"
                  required
                  onChange={(e) =>
                    setForm({ ...form, camera_id: e.target.value })
                  }
                />
                {textInput(
                  form.camera_location,
                  (v) => setForm({ ...form, camera_location: v }),
                  '설치 위치 (예: 1공장 동쪽 벽면)',
                  200,
                )}
                {zoneSelect(form.zone_name, (zone_name) =>
                  setForm({ ...form, zone_name }),
                )}
              </div>
              <details className={styles.more}>
                <summary>공정·촬영 각도 (선택)</summary>
                <div className={styles.formGrid}>
                  {textInput(
                    form.process_type,
                    (v) => setForm({ ...form, process_type: v }),
                    '공정 (예: 유압 성형)',
                    100,
                  )}
                  {textInput(
                    form.camera_angle_type,
                    (v) => setForm({ ...form, camera_angle_type: v }),
                    '촬영 각도 (예: wide)',
                    100,
                  )}
                </div>
              </details>
              <div className={styles.actions}>
                <button
                  type="button"
                  className={styles.btn}
                  onClick={() => {
                    setForm(EMPTY);
                    setAdding(false);
                  }}
                >
                  취소
                </button>
                <button
                  className={styles.primaryBtn}
                  type="submit"
                  disabled={busy || !form.camera_id.trim()}
                >
                  등록
                </button>
              </div>
            </form>
          )}

          {cameras.length === 0 && !adding && (
            <p className={styles.muted}>
              등록된 카메라가 없습니다. 카메라를 등록해야 현장 PC 연결과 이벤트
              수신이 가능합니다.
            </p>
          )}

          <ul className={styles.list}>
            {cameras.map((c) => (
              <li
                key={c.camera_id}
                className={`${styles.item} ${c.status === 'inactive' ? styles.inactive : ''}`}
              >
                {editing === c.camera_id ? (
                  <div className={styles.form}>
                    <p className={styles.name}>{c.camera_id}</p>
                    <div className={styles.formGrid}>
                      {textInput(
                        draft.camera_location,
                        (v) => setDraft({ ...draft, camera_location: v }),
                        '설치 위치',
                        200,
                      )}
                      {zoneSelect(draft.zone_name, (zone_name) =>
                        setDraft({ ...draft, zone_name }),
                      )}
                      {textInput(
                        draft.process_type,
                        (v) => setDraft({ ...draft, process_type: v }),
                        '공정',
                        100,
                      )}
                      {textInput(
                        draft.camera_angle_type,
                        (v) => setDraft({ ...draft, camera_angle_type: v }),
                        '촬영 각도',
                        100,
                      )}
                    </div>
                    <div className={styles.actions}>
                      <button
                        className={styles.btn}
                        onClick={() => setEditing(null)}
                      >
                        취소
                      </button>
                      <button
                        className={styles.primaryBtn}
                        disabled={busy}
                        onClick={() => save(c)}
                      >
                        저장
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    <div className={styles.itemMain}>
                      <span className={styles.name}>
                        {c.camera_id}
                        <span
                          className={`${styles.chip} ${styles[STATUS[c.status].cls]}`}
                        >
                          {STATUS[c.status].label}
                        </span>
                      </span>
                      <span className={styles.meta}>
                        {c.zone_name ?? '구역 미지정'}
                        {c.camera_location && ` · ${c.camera_location}`}
                        {c.process_type && ` · ${c.process_type}`}
                      </span>
                      <span className={styles.meta}>
                        영상 송출 {pcName(c.source_node_id)} · 방송{' '}
                        {pcName(c.output_node_id)}
                      </span>
                      {!c.zone_name && c.status !== 'inactive' && (
                        <span className={styles.warn}>
                          구역이 없어 구역별 규칙과 방송 문구가 적용되지
                          않습니다.
                        </span>
                      )}
                      {!c.source_node_id && c.status !== 'inactive' && (
                        <span className={styles.warn}>
                          영상 송출 PC가 없어 영상을 받을 수 없습니다.{' '}
                          <Link to="/nodes">현장 PC에서 연결</Link>
                        </span>
                      )}
                    </div>
                    <div className={styles.itemActions}>
                      <button
                        className={styles.linkBtn}
                        disabled={busy}
                        onClick={() => startEdit(c)}
                      >
                        수정
                      </button>
                      {c.status === 'active' ? (
                        <ConfirmButton
                          className={styles.dangerLink}
                          disabled={busy}
                          label="중지"
                          question={`${c.camera_id}를 중지할까요? 새 이벤트와 영상이 거절됩니다. 기존 이벤트는 유지됩니다.`}
                          confirmLabel="중지"
                          onConfirm={() => setStatus(c, 'inactive')}
                        />
                      ) : (
                        <button
                          className={styles.linkBtn}
                          disabled={busy}
                          onClick={() => setStatus(c, 'active')}
                        >
                          사용
                        </button>
                      )}
                    </div>
                  </>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {cameras && !MOCK_AUTH && <CameraGroups cameras={cameras} />}
    </div>
  );
}
