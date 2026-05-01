import { useLocation } from 'react-router-dom';
import { AVAILABLE_QUARTERS } from '../mock';
import styles from './TopBar.module.css';

const PAGE_TITLES: Record<string, string> = {
  '/': '홈 요약',
  '/review': '후보 이벤트 검토',
  '/stats': '분기별 통계',
  '/recommend': '교육 추천',
};

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
    </header>
  );
}
