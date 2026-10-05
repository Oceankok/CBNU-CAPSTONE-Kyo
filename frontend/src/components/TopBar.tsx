import { useLocation } from 'react-router-dom';
import { AVAILABLE_QUARTERS } from '../mock';
import { logout } from '../api/auth';
import styles from './TopBar.module.css';

const PAGE_TITLES: Record<string, string> = {
  '/': '홈 요약',
  '/review': '후보 이벤트 검토',
  '/stats': '분기별 통계',
  '/recommend': '교육 추천',
  '/broadcast': '경고 방송 설정',
  '/zones': '구역별 안전 규칙',
};

// Pages whose content depends on the selected quarter
const QUARTER_PAGES = ['/', '/stats', '/recommend'];

interface TopBarProps {
  quarter: string;
  onQuarterChange: (quarter: string) => void;
}

export default function TopBar({ quarter, onQuarterChange }: TopBarProps) {
  const { pathname } = useLocation();

  // Match the closest page title (handles /review/:id as well)
  const title =
    Object.entries(PAGE_TITLES)
      .sort((a, b) => b[0].length - a[0].length)
      .find(([path]) => pathname.startsWith(path))?.[1] ?? '대시보드';

  return (
    <header className={styles.topbar}>
      <h1 className={styles.title}>{title}</h1>
      <div className={styles.actions}>
        {QUARTER_PAGES.includes(pathname) && (
          <select
            className={styles.quarterSelect}
            value={quarter}
            onChange={(e) => onQuarterChange(e.target.value)}
          >
            {AVAILABLE_QUARTERS.map((q) => (
              <option key={q} value={q}>
                {q}
              </option>
            ))}
          </select>
        )}
        {/* Sidebar (with its logout) is a bottom tab bar on mobile, so logout lives here there */}
        <button className={styles.logoutBtn} onClick={logout}>
          로그아웃
        </button>
      </div>
    </header>
  );
}
