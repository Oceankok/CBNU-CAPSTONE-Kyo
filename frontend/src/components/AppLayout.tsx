import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useEffect, useState } from 'react';
import Sidebar from './Sidebar';
import TopBar from './TopBar';
import { usePendingEvents } from '../hooks/usePendingEvents';
import type { CandidateEvent } from '../types';
import styles from './AppLayout.module.css';

export interface LayoutContext {
  quarter: string;
  pending: CandidateEvent[] | null;
  pendingError: string | null;
}

export default function AppLayout() {
  const [quarter, setQuarter] = useState('2026-Q2');
  const { pathname } = useLocation();
  // Path as trigger: re-check after navigating (e.g. returning from a submitted review)
  const { pending, error, newCount, clearNew } = usePendingEvents(pathname);
  const navigate = useNavigate();
  const pendingCount = pending?.length ?? 0;

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
          <Outlet
            context={
              { quarter, pending, pendingError: error } satisfies LayoutContext
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
