import { NavLink, useLocation } from 'react-router-dom';
import { getSession, logout } from '../api/auth';
import { SETTINGS_ITEMS } from '../nav';
import styles from './Sidebar.module.css';

const NAV_ITEMS = [
  { to: '/', label: '홈', end: true },
  { to: '/review', label: '검토' },
  { to: '/stats', label: '통계' },
  { to: '/recommend', label: '교육 추천' },
  { to: '/settings', label: '설정' },
];

// Settings sub-pages keep the 설정 tab highlighted
const SETTINGS_PATHS = ['/settings', ...SETTINGS_ITEMS.map((i) => i.to)];

export default function Sidebar({ pendingCount }: { pendingCount: number }) {
  const session = getSession();
  const { pathname } = useLocation();
  const inSettings = SETTINGS_PATHS.includes(pathname);

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
              `${styles.navItem} ${isActive || (item.to === '/settings' && inSettings) ? styles.active : ''}`
            }
          >
            {item.label}
            {item.to === '/review' && pendingCount > 0 && (
              <span
                className={styles.badge}
                aria-label={`미검토 ${pendingCount}건`}
              >
                {pendingCount}
              </span>
            )}
          </NavLink>
        ))}
      </nav>
      <div className={styles.userInfo}>
        <span className={styles.userName}>{session?.display_name}</span>
        <button className={styles.logoutBtn} onClick={logout}>
          로그아웃
        </button>
      </div>
    </aside>
  );
}
