import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useOutletContext } from 'react-router-dom';
import SummaryCard from '../components/SummaryCard';
import { fetchStats, calcTrend } from '../api/stats';
import type { LayoutContext } from '../components/AppLayout';
import type { QuarterlyStats } from '../types';
import styles from './HomePage.module.css';

// Number of pending events previewed on the home screen; the rest are on /review
const PENDING_PREVIEW = 5;

export default function HomePage() {
  // Pending events are polled by AppLayout, so this list stays live without its own fetch
  const { quarter, pending, pendingError: error } = useOutletContext<LayoutContext>();
  const navigate = useNavigate();

  const [stats, setStats] = useState<QuarterlyStats | null>(null);
  const pendingEvents = pending ?? [];
  const loading = pending === null && !error;

  useEffect(() => {
    // Stats may not exist yet so treat 404 as null
    fetchStats(quarter)
      .then(setStats)
      .catch(() => setStats(null)); // stats 미생성 시 카드 숨김
  }, [quarter]);

  return (
    <div className={styles.page}>
      {error && <p style={{ color: '#e53e3e' }}>⚠ API 오류: {error}</p>}
      {loading && <p style={{ color: '#718096' }}>데이터를 불러오는 중...</p>}

      {/* Primary action: events waiting for a human decision */}
      {pending && (
        <section className={`${styles.section} ${pendingEvents.length ? styles.pendingSection : ''}`}>
          <div className={styles.sectionHeader}>
            <div>
              <span className={styles.pendingLabel}>검토 대기</span>
              <span className={styles.pendingCount}>{pendingEvents.length}건</span>
            </div>
            {pendingEvents.length > 0 && (
              <button className={styles.primaryBtn} onClick={() => navigate('/review')}>
                검토하러 가기 →
              </button>
            )}
          </div>
          {pendingEvents.length === 0 ? (
            <p className={styles.empty}>✓ 모든 이벤트의 검토가 끝났습니다.</p>
          ) : (
            <ul className={styles.pendingList}>
              {pendingEvents.slice(0, PENDING_PREVIEW).map((event) => (
                <li key={event.event_id}>
                  <button
                    className={styles.pendingItem}
                    onClick={() => navigate(`/review/${event.event_id}`)}
                  >
                    <span className={styles.pendingMain}>
                      <strong>{event.ppe_type === 'helmet' ? '안전모' : '안전조끼'} 미착용</strong>
                      <span className={styles.pendingZone}>{event.zone_name}</span>
                    </span>
                    <span className={styles.pendingMeta}>
                      {new Date(event.timestamp_start).toLocaleString('ko-KR', {
                        month: '2-digit',
                        day: '2-digit',
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                      {' · '}
                      {(event.ai_confidence * 100).toFixed(0)}%
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {pendingEvents.length > PENDING_PREVIEW && (
            <button className={styles.viewAll} onClick={() => navigate('/review')}>
              외 {pendingEvents.length - PENDING_PREVIEW}건 더 보기 →
            </button>
          )}
        </section>
      )}

      {stats && (
        <div className={styles.cardRow}>
          <SummaryCard label="확정 위반" value={stats.summary.confirmed_count} trend={calcTrend(stats.trend)} accent />
          <SummaryCard label="보류" value={stats.summary.hold_count} />
          <SummaryCard label="오탐" value={stats.summary.false_positive_count} />
          <SummaryCard label="전체 후보" value={stats.summary.candidate_count} />
        </div>
      )}

      {stats && (
        <div className={styles.breakdownRow}>
          <section className={styles.section}>
            <h3>PPE 유형별 확정 위반</h3>
            <div className={styles.barList}>
              {stats.by_ppe_type.map((p) => (
                <div key={p.ppe_type} className={styles.barItem}>
                  <span className={styles.barName}>{p.ppe_type === 'helmet' ? '안전모' : '안전조끼'}</span>
                  <div className={styles.barWrap}>
                    {/* Width proportional to share of total confirmed violations */}
                    <div
                      className={styles.barFill}
                      style={{
                        width: `${(p.confirmed_count / (stats.summary.confirmed_count || 1)) * 100}%`,
                        backgroundColor: p.ppe_type === 'helmet' ? '#e53e3e' : '#d69e2e',
                      }}
                    />
                  </div>
                  <span className={styles.barCount}>{p.confirmed_count}건</span>
                </div>
              ))}
            </div>
          </section>

          <section className={styles.section}>
            <h3>구역별 확정 위반</h3>
            <div className={styles.barList}>
              {stats.by_zone.map((z) => (
                <div key={z.zone_name} className={styles.barItem}>
                  <span className={styles.barName}>{z.zone_name}</span>
                  <div className={styles.barWrap}>
                    <div
                      className={styles.barFill}
                      style={{
                        width: `${(z.confirmed_count / (stats.by_zone[0]?.confirmed_count || 1)) * 100}%`,
                      }}
                    />
                  </div>
                  <span className={styles.barCount}>{z.confirmed_count}건</span>
                </div>
              ))}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
