import { useState, useMemo, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import FilterBar from '../components/FilterBar';
import StatusBadge from '../components/StatusBadge';
import { fetchEvents } from '../api/events';
import type { FilterState, CandidateEvent } from '../types';
import styles from './ReviewListPage.module.css';

const DEFAULT_FILTERS: FilterState = {
  ppeType: 'all',
  status: 'all',
  zone: [],
  dateFrom: '',
  dateTo: '',
  minConfidence: 0,
};

// pending events always appear first, then sort by timestamp descending
function sortEvents(events: CandidateEvent[]): CandidateEvent[] {
  return [...events].sort((a, b) => {
    if (a.event_status === 'pending' && b.event_status !== 'pending') return -1;
    if (a.event_status !== 'pending' && b.event_status === 'pending') return 1;
    return new Date(b.timestamp_start).getTime() - new Date(a.timestamp_start).getTime();
  });
}

function applyFilters(events: CandidateEvent[], f: FilterState): CandidateEvent[] {
  return events.filter((e) => {
    if (f.ppeType !== 'all' && e.ppe_type !== f.ppeType) return false;
    if (f.status !== 'all' && e.event_status !== f.status) return false;
    if (f.zone.length > 0 && !f.zone.includes(e.zone_name)) return false;
    if (f.dateFrom && e.timestamp_start < f.dateFrom) return false;
    if (f.dateTo && e.timestamp_start > f.dateTo + 'T23:59:59') return false;
    if (e.ai_confidence < f.minConfidence) return false;
    return true;
  });
}

export default function ReviewListPage() {
  const navigate = useNavigate();
  const [filters, setFilters] = useState<FilterState>(DEFAULT_FILTERS);
  const [events, setEvents] = useState<CandidateEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchEvents()
      .then((data) => setEvents(data.items))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const visibleEvents = useMemo(() => sortEvents(applyFilters(events, filters)), [events, filters]);
  const pendingCount = events.filter((e) => e.event_status === 'pending').length;

  return (
    <div className={styles.page}>
      {error && <div className={styles.pendingBanner}>⚠ API 오류: {error}</div>}
      {loading && <div className={styles.pendingBanner}>이벤트 목록을 불러오는 중...</div>}
      {!loading && pendingCount > 0 && (
        <div className={styles.pendingBanner}>
          미검토 이벤트가 <strong>{pendingCount}건</strong> 있습니다. 확인 후 검토해 주세요.
        </div>
      )}

      <FilterBar filters={filters} onChange={setFilters} />

      <div className={styles.tableWrapper}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>이벤트 ID</th>
              <th>발생 일시</th>
              <th>구역</th>
              <th>PPE 유형</th>
              <th>지속시간</th>
              <th>신뢰도</th>
              <th>상태</th>
            </tr>
          </thead>
          <tbody>
            {visibleEvents.length === 0 ? (
              <tr>
                <td colSpan={7} className={styles.empty}>
                  조건에 맞는 이벤트가 없습니다.
                </td>
              </tr>
            ) : (
              visibleEvents.map((event) => (
                <tr
                  key={event.event_id}
                  className={`${styles.row} ${event.event_status === 'pending' ? styles.pendingRow : ''}`}
                  onClick={() => navigate(`/review/${event.event_id}`)}
                >
                  <td className={styles.eventId}>{event.event_id}</td>
                  <td>{new Date(event.timestamp_start).toLocaleString('ko-KR')}</td>
                  <td>{event.zone_name}</td>
                  <td>
                    <span className={`${styles.ppeTag} ${styles[event.ppe_type]}`}>
                      {event.ppe_type === 'helmet' ? '안전모' : '안전조끼'}
                    </span>
                  </td>
                  <td>{event.duration_sec}초</td>
                  <td>
                    <span
                      className={`${styles.confidence} ${
                        event.ai_confidence >= 0.85
                          ? styles.high
                          : event.ai_confidence >= 0.7
                            ? styles.mid
                            : styles.low
                      }`}
                    >
                      {(event.ai_confidence * 100).toFixed(0)}%
                    </span>
                  </td>
                  <td>
                    <StatusBadge status={event.event_status} />
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <p className={styles.count}>
        {visibleEvents.length}건 표시 중 (전체 {events.length}건)
      </p>
    </div>
  );
}
