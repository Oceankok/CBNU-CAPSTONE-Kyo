import { useEffect, useState } from 'react';
import { getSession } from '../api/auth';
import {
  fetchUsers,
  createUser,
  updateUser,
  resetPassword,
} from '../api/users';
import type { AppUser, NewUser, UserRole } from '../types';
import ConfirmButton from '../components/ConfirmButton';
import styles from './UsersPage.module.css';

const ROLE_LABEL: Record<UserRole, string> = {
  admin: '관리자',
  worker: '작업자',
};
const EMPTY_FORM: NewUser = {
  user_id: '',
  display_name: '',
  role: 'worker',
  zone_names: [],
  password: '',
};

export default function UsersPage() {
  const me = getSession()?.user_id;
  const [users, setUsers] = useState<AppUser[] | null>(null);
  // One message at a time: a new success clears the last error and vice versa
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const fail = (text: string) => setMsg({ ok: false, text });
  const done = (text: string) => setMsg({ ok: true, text });
  const [busy, setBusy] = useState<string | null>(null); // user_id being updated
  const [form, setForm] = useState<NewUser>(EMPTY_FORM);
  // Inline password reset (window.prompt is blocked in some embedded browsers)
  const [resetFor, setResetFor] = useState<string | null>(null);
  const [newPw, setNewPw] = useState('');

  useEffect(() => {
    fetchUsers()
      .then(setUsers)
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
    if (newPw.length < 8) return fail('비밀번호는 8자 이상이어야 합니다.');
    resetPassword(u.user_id, newPw)
      .then(() => {
        setResetFor(null);
        setNewPw('');
        done(
          `${u.user_id} 비밀번호를 재설정했습니다. 기존 로그인은 해제됩니다.`,
        );
      })
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
    createUser(body)
      .then((created) => {
        setUsers((prev) => [...(prev ?? []), created]);
        setForm(EMPTY_FORM);
        done(`${created.user_id} 계정을 만들었습니다.`);
      })
      .catch((err: Error) => fail(`계정 생성 실패: ${err.message}`));
  };

  return (
    <div className={styles.page}>
      <p className={styles.desc}>
        계정 추가·정지와 비밀번호를 관리합니다. 담당 구역은 설정 → 구역 규칙에서
        구역별로 지정합니다.
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

              {/* Zones are assigned from the zone screen; shown here for reference */}
              <span className={styles.zone}>
                {u.role === 'worker'
                  ? u.zone_names.length
                    ? u.zone_names.join(', ')
                    : '구역 미지정'
                  : '—'}
              </span>

              <div className={styles.rowActions}>
                <span
                  className={`${styles.state} ${u.is_active ? styles.stateOn : styles.stateOff}`}
                >
                  {u.is_active ? '사용 중' : '정지됨'}
                </span>
                {u.user_id === me ? (
                  <span className={styles.self}>본인 계정</span>
                ) : u.is_active ? (
                  <ConfirmButton
                    className={styles.dangerLink}
                    disabled={busy === u.user_id}
                    label="계정 정지"
                    question={`${u.display_name} 계정을 정지할까요? 바로 로그아웃되고 다시 로그인할 수 없습니다.`}
                    confirmLabel="정지"
                    onConfirm={() => update(u, { is_active: false })}
                  />
                ) : (
                  <button
                    className={styles.linkBtn}
                    disabled={busy === u.user_id}
                    onClick={() => update(u, { is_active: true })}
                  >
                    정지 해제
                  </button>
                )}
                <button
                  className={styles.linkBtn}
                  onClick={() => {
                    setResetFor(resetFor === u.user_id ? null : u.user_id);
                    setNewPw('');
                  }}
                >
                  비밀번호 재설정
                </button>
              </div>

              {resetFor === u.user_id && (
                <form
                  className={styles.resetRow}
                  onSubmit={(e) => {
                    e.preventDefault();
                    handleReset(u);
                  }}
                >
                  <input
                    className={styles.input}
                    type="password"
                    placeholder="새 비밀번호 (8자 이상)"
                    value={newPw}
                    onChange={(e) => setNewPw(e.target.value)}
                    autoComplete="new-password"
                    autoFocus
                  />
                  <button
                    type="button"
                    className={styles.linkBtn}
                    onClick={() => setResetFor(null)}
                  >
                    취소
                  </button>
                  <button
                    type="submit"
                    className={styles.primaryBtn}
                    disabled={newPw.length < 8}
                  >
                    변경
                  </button>
                </form>
              )}
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
