import { useEffect, useState } from 'react';
import ConfirmButton from './ConfirmButton';
import {
  GROUP_ERRORS,
  createCameraGroup,
  deleteCameraGroup,
  fetchCameraGroups,
  updateCameraGroup,
} from '../api/cameraGroups';
import type { CameraGroup } from '../api/cameraGroups';
import type { Camera } from '../api/cameras';
import styles from './CameraGroups.module.css';

// Admin groups cameras that film the same spot. A new event on one camera then asks the
// others for a reference clip. A group never decides that two events are the same incident (#138).
export default function CameraGroups({ cameras }: { cameras: Camera[] }) {
  const [groups, setGroups] = useState<CameraGroup[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // 'new' = creating; otherwise the group being edited
  const [editing, setEditing] = useState<CameraGroup | 'new' | null>(null);
  const [name, setName] = useState('');
  const [picked, setPicked] = useState<string[]>([]);

  const fail = (e: Error) => setError(GROUP_ERRORS[e.message] ?? e.message);
  const load = () => fetchCameraGroups().then(setGroups).catch(fail);

  useEffect(() => {
    void fetchCameraGroups()
      .then(setGroups)
      .catch((e: Error) => setError(GROUP_ERRORS[e.message] ?? e.message));
  }, []);

  const start = (g: CameraGroup | 'new') => {
    setEditing(g);
    setError(null);
    setName(g === 'new' ? '' : g.name);
    setPicked(g === 'new' ? [] : g.camera_ids);
  };

  const toggle = (id: string) =>
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));

  const save = () => {
    if (!editing) return;
    setBusy(true);
    setError(null);
    const body = { name: name.trim(), camera_ids: picked };
    (editing === 'new'
      ? createCameraGroup(body)
      : updateCameraGroup(editing.group_id, body, editing.version)
    )
      .then(() => {
        setEditing(null);
        return load();
      })
      .catch(fail)
      .finally(() => setBusy(false));
  };

  const remove = (g: CameraGroup) => {
    setBusy(true);
    setError(null);
    deleteCameraGroup(g.group_id, g.version)
      .then(() => load())
      .catch(fail)
      .finally(() => setBusy(false));
  };

  const label = (id: string) => {
    const c = cameras.find((x) => x.camera_id === id);
    return c?.camera_location ? `${id} (${c.camera_location})` : id;
  };

  return (
    <section className={styles.card}>
      <div className={styles.head}>
        <h3 className={styles.title}>카메라 그룹 ({groups?.length ?? 0})</h3>
        {!editing && (
          <button
            type="button"
            className={styles.btn}
            onClick={() => start('new')}
          >
            + 그룹 만들기
          </button>
        )}
      </div>
      <p className={styles.help}>
        같은 장소를 서로 다른 각도에서 찍는 카메라를 묶습니다. 한 카메라에서 새
        이벤트가 생기면 같은 그룹의 다른 카메라에도 참고 영상 요청이 만들어져,
        검토할 때 여러 각도를 함께 볼 수 있습니다. 그룹이 같은 사건이라는 뜻은
        아니며, 별도로 생긴 이벤트는 따로 유지됩니다. 과거 이벤트에는 적용되지
        않습니다.
      </p>
      {error && <p className={styles.error}>⚠ {error}</p>}
      {!groups && !error && <p className={styles.help}>불러오는 중...</p>}
      {groups?.length === 0 && !editing && (
        <p className={styles.help}>만든 그룹이 없습니다.</p>
      )}

      {groups?.map((g) =>
        editing !== 'new' && editing?.group_id === g.group_id ? null : (
          <div key={g.group_id} className={styles.item}>
            <strong className={styles.name}>{g.name}</strong>
            <div className={styles.chips}>
              {g.camera_ids.map((id) => (
                <span key={id} className={styles.chip}>
                  {label(id)}
                  {g.cameras.find((c) => c.camera_id === id)?.status ===
                    'inactive' && ' (중지)'}
                </span>
              ))}
            </div>
            {!editing && (
              <div className={styles.actions}>
                <button
                  type="button"
                  className={styles.btn}
                  disabled={busy}
                  onClick={() => start(g)}
                >
                  수정
                </button>
                <ConfirmButton
                  className={styles.danger}
                  label="삭제"
                  question="그룹을 삭제할까요? 이미 만들어진 참고 요청은 유지됩니다."
                  confirmLabel="삭제"
                  disabled={busy}
                  onConfirm={() => remove(g)}
                />
              </div>
            )}
          </div>
        ),
      )}

      {editing && (
        <div className={styles.form}>
          <label className={styles.field}>
            그룹 이름
            <input
              className={styles.input}
              value={name}
              maxLength={200}
              placeholder="예: 프레스 작업 위치"
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <div className={styles.field}>
            카메라 (2대 이상)
            <ul className={styles.list}>
              {cameras.map((c) => (
                <li key={c.camera_id}>
                  <label className={styles.pick}>
                    <input
                      type="checkbox"
                      checked={picked.includes(c.camera_id)}
                      onChange={() => toggle(c.camera_id)}
                    />
                    {label(c.camera_id)}
                    {c.zone_name && (
                      <span className={styles.sub}> · {c.zone_name}</span>
                    )}
                    {c.status === 'inactive' && (
                      <span className={styles.sub}> · 중지됨</span>
                    )}
                  </label>
                </li>
              ))}
            </ul>
          </div>
          <div className={styles.actions}>
            <button
              type="button"
              className={styles.btn}
              onClick={() => setEditing(null)}
            >
              취소
            </button>
            <button
              type="button"
              className={styles.primary}
              disabled={busy || !name.trim() || picked.length < 2}
              onClick={save}
            >
              {editing === 'new' ? '그룹 만들기' : '저장'}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
