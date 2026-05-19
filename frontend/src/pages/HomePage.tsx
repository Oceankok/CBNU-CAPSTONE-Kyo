import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useOutletContext } from 'react-router-dom';
import SummaryCard from '../components/SummaryCard';
import StatusBadge from '../components/StatusBadge';
import { fetchEvents } from '../api/events';
import { fetchStats } from '../api/stats';
import type { CandidateEvent, QuarterlyStats } from '../types';
import styles from './HomePage.module.css';

export default function HomePage() {
  const { quarter } = useOutletContext<{ quarter: string }>();
  const navigate = useNavigate();

  const [stats, setStats] = useState<QuarterlyStats | null>(null);
  const [pendingEvents, setPendingEvents] = useState<CandidateEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    Promise.all([fetchStats(quarter), fetchEvents()])
      .then(([s, evData]) => {
        setStats(s);
        // Show only pending events as priority items
        setPendingEvents(evData.items.filter((e) => e.event_status === 'pending'));
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [quarter]);

  return (
    <div className={styles.page}>
      <p className={styles.quarterLabel}>{quarter} 기준</p>

      {error && <p style={{ color: '#e53e3e' }}>⚠ API 오류: {error}</p>}
      {loading && <p style={{ color: '#718096' }}>데이터를 불러오는 중...</p>}

      {stats && (
        <div className={styles.cardRow}>
          <SummaryCard label="후보 이벤트" value={stats.summary.candidate_count} sub="AI 추출 총합" />
          <SummaryCard label="확정 위반" value={stats.summary.confirmed_count} sub="검토 완료" trend={12} />
          <SummaryCard label="오탐" value={stats.summary.false_positive_count} sub="AI 오탐지" trend={-5} />
          <SummaryCard label="보류" value={stats.summary.hold_count} sub="추가 검토 필요" />
        </div>
      )}

      <section className={styles.section}>
        <div className={styles.sectionHeader}>
          <h3>미검토 이벤트 ({pendingEvents.length}건)</h3>
          <button className={styles.viewAll} onClick={() => navigate('/review')}>
            전체 보기 →
          </button>
        </div>
        {pendingEvents.length === 0 ? (
          <p className={styles.empty}>미검토 이벤트가 없습니다.</p>
        ) : (
          <table className={styles.table}>
            <thead>
              <tr>
                <th>이벤트 ID</th>
                <th>발생 일시</th>
                <th>구역</th>
                <th>PPE 유형</th>
                <th>신뢰도</th>
                <th>상태</th>
              </tr>
            </thead>
            <tbody>
              {pendingEvents.map((event) => (
                <tr
                  key={event.event_id}
                  className={styles.row}
                  onClick={() => navigate(`/review/${event.event_id}`)}
                >
                  <td className={styles.eventId}>{event.event_id}</td>
                  <td>
                    {new Date(event.timestamp_start).toLocaleString('ko-KR', {
                      month: '2-digit',
                      day: '2-digit',
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </td>
                  <td>{event.zone_name}</td>
                  <td>{event.ppe_type === 'helmet' ? '안전모' : '안전조끼'}</td>
                  <td>{(event.ai_confidence * 100).toFixed(0)}%</td>
                  <td>
                    <StatusBadge status={event.event_status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className={styles.section}>
        <h3>PPE 유형별 위반 현황</h3>
        <div className={styles.ppeRow}>
          {stats?.by_ppe_type.map((p) => (
            <div key={p.ppe_type} className={styles.ppeCard}>
              <div className={styles.ppeCardHeader}>
                <span className={styles.ppeName}>
                  {p.ppe_type === 'helmet' ? '🪖 안전모' : '🦺 안전조끼'}
                </span>
                <span className={styles.ppeCount}>{p.confirmed_count}건</span>
              </div>
              <div className={styles.ppeBar}>
                {/* Width proportional to share of total confirmed violations */}
                <div
                  className={styles.ppeBarFill}
                  style={{
                    width: `${(p.confirmed_count / (stats.summary.confirmed_count || 1)) * 100}%`,
                    backgroundColor: p.ppe_type === 'helmet' ? '#e53e3e' : '#d69e2e',
                  }}
                />
              </div>
              <span className={styles.ppeScore}>우선순위 점수 {p.priority_score}</span>
            </div>
          ))}
        </div>
      </section>

      <section className={styles.section}>
        <h3>구역별 위반 현황</h3>
        <div className={styles.zoneList}>
          {stats?.by_zone.map((z, idx) => (
            <div key={z.zone_name} className={styles.zoneItem}>
              <span className={styles.zoneRank}>#{idx + 1}</span>
              <span className={styles.zoneName}>{z.zone_name}</span>
              <div className={styles.zoneBarWrap}>
                <div
                  className={styles.zoneBarFill}
                  style={{
                    width: `${(z.confirmed_count / (stats.by_zone[0]?.confirmed_count || 1)) * 100}%`,
                  }}
                />
              </div>
              <span className={styles.zoneCount}>{z.confirmed_count}건</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
