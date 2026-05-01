import { Outlet } from 'react-router-dom';
import { useState } from 'react';
import Sidebar from './Sidebar';
import TopBar from './TopBar';
import styles from './AppLayout.module.css';

export default function AppLayout() {
  const [quarter, setQuarter] = useState('2026-Q2');

  return (
    <div className={styles.layout}>
      <Sidebar />
      <div className={styles.main}>
        <TopBar quarter={quarter} onQuarterChange={setQuarter} />
        <main className={styles.content}>
          {/* Pass selected quarter to child pages via context if needed */}
          <Outlet context={{ quarter }} />
        </main>
      </div>
    </div>
  );
}
