import { NavLink } from 'react-router-dom';
import styles from './Sidebar.module.css';

const NAV_ITEMS = [
  { to: '/', label: '홈', end: true },
  { to: '/review', label: '검토' },
  { to: '/stats', label: '통계' },
  { to: '/recommend', label: '교육 추천' },
  { to: '/broadcast', label: '경고 방송' },
];

export default function Sidebar() {
  return (
    <aside className={styles.sidebar}>
      <div className={styles.logo}>
        <span className={styles.logoIcon}>🛡️</span>
        <span className={styles.logoText}>SENTINEL AI</span>
      </div>
      <nav className={styles.nav}>
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) =>
              `${styles.navItem} ${isActive ? styles.active : ''}`
            }
          >
            {item.label}
          </NavLink>
        ))}
      </nav>
      <div className={styles.userInfo}>
        <span className={styles.userName}>admin01</span>
        <button className={styles.logoutBtn}>로그아웃</button>
      </div>
    </aside>
  );
}
