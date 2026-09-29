import { getSession, logout } from '../api/auth';
import styles from './WorkerHomePage.module.css';

// ponytail: placeholder shell — zone rules / alerts / education screens land here once the backend APIs exist
export default function WorkerHomePage() {
  const session = getSession();

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <span className={styles.name}>{session?.display_name}</span>
        <button className={styles.logout} onClick={logout}>
          로그아웃
        </button>
      </header>
      <main className={styles.content}>
        <section className={styles.card}>
          <h2>내 구역 안전 규칙</h2>
          <p>준비 중입니다.</p>
        </section>
        <section className={styles.card}>
          <h2>안전 교육 자료</h2>
          <p>준비 중입니다.</p>
        </section>
      </main>
    </div>
  );
}
