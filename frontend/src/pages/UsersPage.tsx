import { useEffect, useState } from 'react';
import { getSession } from '../api/auth';
import {
  fetchUsers,
  createUser,
  updateUser,
  resetPassword,
} from '../api/users';
import { fetchZones } from '../api/zones';
import type { AppUser, NewUser, UserRole } from '../types';
import styles from './UsersPage.module.css';

const ROLE_LABEL: Record<UserRole, string> = {
  admin: '관리자',
  worker: '작업자',
};
const EMPTY_FORM: NewUser = {
  user_id: '',
  display_name: '',
  role: 'worker',
  zone_name: null,
  password: '',
};

export default function UsersPage() {
  const me = getSession()?.user_id;
  const [users, setUsers] = useState<AppUser[] | null>(null);
  const [zones, setZones] = useState<string[]>([]);
  // One message at a time: a new success clears the last error and vice versa
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const fail = (text: string) => setMsg({ ok: false, text });
  const done = (text: string) => setMsg({ ok: true, text });
  const [busy, setBusy] = useState<string | null>(null); // user_id being updated
  const [form, setForm] = useState<NewUser>(EMPTY_FORM);

  useEffect(() => {
    Promise.all([fetchUsers(), fetchZones()])
      .then(([u, z]) => {
        setUsers(u);
        setZones(z.map((x) => x.zone_name));
      })
      .catch((e: Error) => fail(e.message));
  }, []);

  // Changes apply immediately — one field at a time, no separate save step
  const update = (u: AppUser, patch: Partial<AppUser>) => {
    setBusy(u.user_id);
    setMsg(null);
    updateUser(u.user_id, patch)
      .then((saved) =>
        setUsers(
          (prev) =>
            prev?.map((x) => (x.user_id === saved.user_id ? saved : x)) ?? prev,
        ),
      )
      .catch((e: Error) => fail(`${u.user_id} 변경 실패: ${e.message}`))
      .finally(() => setBusy(null));
  };

  const handleReset = (u: AppUser) => {
    const pw = window.prompt(
      `${u.display_name}(${u.user_id})의 새 비밀번호 (8자 이상)`,
    );
    if (pw === null) return;
    if (pw.length < 8) return fail('비밀번호는 8자 이상이어야 합니다.');
    resetPassword(u.user_id, pw)
      .then(() =>
        done(
          `${u.user_id} 비밀번호를 재설정했습니다. 기존 로그인은 해제됩니다.`,
        ),
      )
      .catch((e: Error) =>
        fail(`${u.user_id} 비밀번호 재설정 실패: ${e.message}`),
      );
  };

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    if (form.password.length < 8)
      return fail('비밀번호는 8자 이상이어야 합니다.');
    setMsg(null);
    const body = {
      ...form,
      user_id: form.user_id.trim(),
      display_name: form.display_name.trim(),
    };
    // Admins have no work zone
    if (body.role === 'admin') body.zone_name = null;
    createUser(body)
      .then((created) => {
        setUsers((prev) => [...(prev ?? []), created]);
        setForm(EMPTY_FORM);
        done(`${created.user_id} 계정을 만들었습니다.`);
      })
      .catch((err: Error) => fail(`계정 생성 실패: ${err.message}`));
  };

  const zoneSelect = (
    value: string | null,
    onChange: (v: string | null) => void,
    disabled = false,
  ) => (
    <select
      className={styles.select}
      value={value ?? ''}
      onChange={(e) => onChange(e.target.value || null)}
      disabled={disabled}
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

  return (
    <div className={styles.page}>
      <p className={styles.desc}>
        작업자의 담당 구역을 지정하면 작업자 화면에 해당 구역의 안전 규칙이
        표시됩니다.
      </p>

      {msg && (
        <p className={msg.ok ? styles.successMsg : styles.errorMsg}>
          {msg.ok ? '✓' : '⚠'} {msg.text}
        </p>
      )}
      {!users && !msg && <p className={styles.desc}>불러오는 중...</p>}

      {users && (
        <ul className={styles.list}>
          {users.map((u) => (
            <li
              key={u.user_id}
              className={`${styles.row} ${u.is_active ? '' : styles.inactive}`}
            >
              <div className={styles.who}>
                <span className={styles.name}>{u.display_name}</span>
                <span className={styles.id}>{u.user_id}</span>
                <span className={`${styles.role} ${styles[u.role]}`}>
                  {ROLE_LABEL[u.role]}
                </span>
              </div>

              {u.role === 'worker' ? (
                zoneSelect(
                  u.zone_name,
                  (zone_name) => update(u, { zone_name }),
                  busy === u.user_id,
                )
              ) : (
                <span className={styles.noZone}>—</span>
              )}

              <div className={styles.rowActions}>
                <label
                  className={styles.activeToggle}
                  title={
                    u.user_id === me ? '본인 계정은 비활성화할 수 없습니다' : ''
                  }
                >
                  <input
                    type="checkbox"
                    checked={u.is_active}
                    disabled={u.user_id === me || busy === u.user_id}
                    onChange={(e) => update(u, { is_active: e.target.checked })}
                  />
                  {u.is_active ? '사용 중' : '정지됨'}
                </label>
                <button
                  className={styles.linkBtn}
                  onClick={() => handleReset(u)}
                >
                  비밀번호 재설정
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {users && (
        <form className={styles.card} onSubmit={handleCreate}>
          <h3 className={styles.cardTitle}>계정 추가</h3>
          <div className={styles.formGrid}>
            <input
              className={styles.input}
              placeholder="아이디"
              value={form.user_id}
              onChange={(e) => setForm({ ...form, user_id: e.target.value })}
              autoCapitalize="none"
              required
            />
            <input
              className={styles.input}
              placeholder="이름"
              value={form.display_name}
              onChange={(e) =>
                setForm({ ...form, display_name: e.target.value })
              }
              required
            />
            <select
              className={styles.select}
              value={form.role}
              onChange={(e) =>
                setForm({ ...form, role: e.target.value as UserRole })
              }
            >
              <option value="worker">작업자</option>
              <option value="admin">관리자</option>
            </select>
            {zoneSelect(
              form.zone_name,
              (zone_name) => setForm({ ...form, zone_name }),
              form.role === 'admin',
            )}
            <input
              className={styles.input}
              type="password"
              placeholder="초기 비밀번호 (8자 이상)"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              autoComplete="new-password"
              required
            />
            <button className={styles.primaryBtn} type="submit">
              추가
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
