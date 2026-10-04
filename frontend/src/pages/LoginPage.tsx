import { useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { getSession, login, homePath, MOCK_AUTH } from '../api/auth';
import styles from './LoginPage.module.css';

export default function LoginPage() {
  const navigate = useNavigate();
  const [userId, setUserId] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Already logged in — skip the form
  const session = getSession();
  if (session) return <Navigate to={homePath(session.role)} replace />;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const s = await login(userId.trim(), password);
      navigate(homePath(s.role), { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : '로그인에 실패했습니다.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className={styles.page}>
      <form className={styles.card} onSubmit={handleSubmit}>
        <div className={styles.logo}>
          <span className={styles.logoIcon}>🛡️</span>
          <span className={styles.logoText}>SENTINEL AI</span>
        </div>

        <label className={styles.field}>
          <span className={styles.label}>아이디</span>
          <input
            className={styles.input}
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
            autoComplete="username"
            autoCapitalize="none"
            required
          />
        </label>

        <label className={styles.field}>
          <span className={styles.label}>비밀번호</span>
          <input
            className={styles.input}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>

        {error && (
          <p className={styles.error} role="alert">
            {error}
          </p>
        )}

        <button className={styles.submit} type="submit" disabled={submitting}>
          {submitting ? '로그인 중…' : '로그인'}
        </button>

        {MOCK_AUTH && (
          <p className={styles.hint}>개발용 계정: admin01 / worker01 (비밀번호 아무거나)</p>
        )}
      </form>
    </div>
  );
}
