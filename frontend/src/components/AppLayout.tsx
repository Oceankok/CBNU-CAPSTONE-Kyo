import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useEffect, useState } from 'react';
import Sidebar from './Sidebar';
import TopBar from './TopBar';
import { usePendingEvents } from '../hooks/usePendingEvents';
import type { PendingEvent } from '../api/events';
import { seoulQuarterNow } from '../time';
import styles from './AppLayout.module.css';

export interface LayoutContext {
  quarter: string;
  // Newest unreviewed events (at most 50); pendingCount is the real total
  pending: PendingEvent[] | null;
  pendingCount: number;
  zones: string[];
  pendingError: string | null;
}

export default function AppLayout() {
  const [quarter, setQuarter] = useState(() => seoulQuarterNow());
  const { pathname } = useLocation();
  // Path as trigger: re-check after navigating (e.g. returning from a submitted review)
  const { pending, pendingCount, zones, error, newCount, clearNew } =
    usePendingEvents(pathname);
  const navigate = useNavigate();

  // Unreviewed count in the browser tab title, visible while the tab is in the background
  useEffect(() => {
    document.title = pendingCount
      ? `(${pendingCount}) SENTINEL AI`
      : 'SENTINEL AI';
  }, [pendingCount]);

  const openReview = () => {
    clearNew();
    navigate('/review');
  };

  return (
    <div className={styles.layout}>
      <Sidebar pendingCount={pendingCount} />
      <div className={styles.main}>
        <TopBar quarter={quarter} onQuarterChange={setQuarter} />
        <main className={styles.content}>
          {/* key: pages remount per quarter, so each starts in its loading state */}
          <Outlet
            key={quarter}
            context={
              {
                quarter,
                pending,
                pendingCount,
                zones,
                pendingError: error,
              } satisfies LayoutContext
            }
          />
        </main>
      </div>

      {newCount > 0 && (
        <div className={styles.toast} role="status">
          <button className={styles.toastMain} onClick={openReview}>
            🔔 새 미검토 이벤트 {newCount}건
            <span className={styles.toastAction}>검토하기 →</span>
          </button>
          <button
            className={styles.toastClose}
            onClick={clearNew}
            aria-label="알림 닫기"
          >
            ✕
          </button>
        </div>
      )}
    </div>
  );
}
