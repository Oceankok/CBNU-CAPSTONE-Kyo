import { useState, useMemo, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import FilterBar from '../components/FilterBar';
import StatusBadge from '../components/StatusBadge';
import { fetchEvents } from '../api/events';
import type { FilterState, CandidateEvent } from '../types';
import { formatEventTime } from '../time';
import { useSeenEvents } from '../seenEvents';
import {
  REVIEW_NEEDED_HINT,
  confidenceLevel,
  formatConfidence,
  isReviewOnly,
  passesConfidenceFilter,
} from '../confidence';
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
    if (!passesConfidenceFilter(e, f.minConfidence)) return false;
    return true;
  });
}

export default function ReviewListPage() {
  const navigate = useNavigate();
  const [filters, setFilters] = useState<FilterState>(DEFAULT_FILTERS);
  const [events, setEvents] = useState<CandidateEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { isUnseen, markSeen } = useSeenEvents();

  // The date range is filtered by the server so it uses Korea calendar days
  const { dateFrom, dateTo } = filters;
  useEffect(() => {
    // A slower, older request must not overwrite the result of the latest dates
    let stale = false;
    fetchEvents(dateFrom, dateTo)
      .then((data) => {
        if (stale) return;
        setEvents(data.items);
        setError(null);
      })
      .catch((e) => {
        if (!stale) setError(e.message);
      })
      .finally(() => {
        if (!stale) setLoading(false);
      });
    return () => {
      stale = true;
    };
  }, [dateFrom, dateTo]);

  const visibleEvents = useMemo(() => sortEvents(applyFilters(events, filters)), [events, filters]);
  const pendingCount = events.filter((e) => e.event_status === 'pending').length;
  const unseenEvents = events.filter(isUnseen);
  // Extract unique zone names from fetched events for dynamic filter options
  const availableZones = useMemo(
    () => [...new Set(events.map((e) => e.zone_name))].sort(),
    [events]
  );

  return (
    <div className={styles.page}>
      {error && <div className={styles.pendingBanner}>⚠ API 오류: {error}</div>}
      {loading && <div className={styles.pendingBanner}>이벤트 목록을 불러오는 중...</div>}
      {!loading && pendingCount > 0 && (
        <div className={styles.pendingBanner}>
          미검토 이벤트가 <strong>{pendingCount}건</strong> 있습니다. 확인 후 검토해 주세요.
          {unseenEvents.length > 0 && (
            <>
              {' '}
              아직 열어 보지 않은 이벤트는 <strong>{unseenEvents.length}건</strong>입니다.
              <button
                type="button"
                className={styles.markAllBtn}
                onClick={() => markSeen(unseenEvents.map((e) => e.event_id))}
              >
                모두 확인함으로 표시
              </button>
            </>
          )}
        </div>
      )}

      <FilterBar filters={filters} onChange={setFilters} zones={availableZones} />

      <div className={styles.tableWrapper}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>이벤트 ID</th>
              <th>발생 일시</th>
              <th>구역</th>
              <th>PPE 유형</th>
              <th className={styles.minor}>지속시간</th>
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
                  className={`${styles.row} ${event.event_status === 'pending' ? styles.pendingRow : ''} ${isUnseen(event) ? styles.unseenRow : ''}`}
                  onClick={() => navigate(`/review/${event.event_id}`)}
                >
                  <td className={styles.eventId}>
                    {event.event_id}
                    {isUnseen(event) && <span className={styles.unseenTag}>미확인</span>}
                  </td>
                  <td data-label="발생 일시">{formatEventTime(event.timestamp_start, event.timestamp_timezone)}</td>
                  <td data-label="구역">{event.zone_name}</td>
                  <td data-label="PPE 유형">
                    <span className={`${styles.ppeTag} ${styles[event.ppe_type]}`}>
                      {event.ppe_type === 'helmet' ? '안전모' : '안전조끼'}
                    </span>
                  </td>
                  <td data-label="지속시간" className={styles.minor}>{event.duration_sec}초</td>
                  <td data-label="신뢰도">
                    {isReviewOnly(event) || event.ai_confidence == null ? (
                      <span
                        className={`${styles.confidence} ${styles.reviewNeeded}`}
                        title={REVIEW_NEEDED_HINT}
                      >
                        {formatConfidence(event)}
                      </span>
                    ) : (
                      <span
                        className={`${styles.confidence} ${styles[confidenceLevel(event.ai_confidence)]}`}
                      >
                        {formatConfidence(event)}
                      </span>
                    )}
                  </td>
                  <td data-label="상태">
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
