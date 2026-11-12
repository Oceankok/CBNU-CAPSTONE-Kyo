import { useState, useEffect } from 'react';
import {
  useNavigate,
  useOutletContext,
  useSearchParams,
} from 'react-router-dom';
import FilterBar from '../components/FilterBar';
import StatusBadge from '../components/StatusBadge';
import { fetchEventsPage, fetchPendingEventIds } from '../api/events';
import type { LayoutContext } from '../components/AppLayout';
import type {
  FilterState,
  CandidateEvent,
  EventStatus,
  PpeType,
} from '../types';
import { formatEventTime } from '../time';
import { useSeenEvents } from '../seenEvents';
import {
  REVIEW_NEEDED_HINT,
  confidenceLevel,
  formatConfidence,
  isReviewOnly,
} from '../confidence';
import styles from './ReviewListPage.module.css';

// Filters and the page live in the address so reload and back keep the same view (#223)
function readFilters(q: URLSearchParams): FilterState {
  const conf = Number(q.get('conf'));
  return {
    status: (q.get('status') as EventStatus | null) ?? 'all',
    ppeType: (q.get('ppe') as PpeType | null) ?? 'all',
    zone: q.get('zone') ?? '',
    dateFrom: q.get('from') ?? '',
    dateTo: q.get('to') ?? '',
    minConfidence: Number.isFinite(conf) && conf > 0 && conf <= 1 ? conf : 0,
  };
}

function writeFilters(f: FilterState, page: number): URLSearchParams {
  const q = new URLSearchParams();
  if (f.status !== 'all') q.set('status', f.status);
  if (f.ppeType !== 'all') q.set('ppe', f.ppeType);
  if (f.zone) q.set('zone', f.zone);
  if (f.dateFrom) q.set('from', f.dateFrom);
  if (f.dateTo) q.set('to', f.dateTo);
  if (f.minConfidence > 0) q.set('conf', String(f.minConfidence));
  if (page > 1) q.set('page', String(page));
  return q;
}

export default function ReviewListPage() {
  const navigate = useNavigate();
  const { pending, pendingCount, zones } = useOutletContext<LayoutContext>();
  const [params, setParams] = useSearchParams();
  const filters = readFilters(params);
  const page = Math.max(1, Math.floor(Number(params.get('page'))) || 1);
  // Phones get shorter pages; decided once so the page boundaries stay stable
  const [pageSize] = useState(() =>
    window.matchMedia('(max-width: 768px)').matches ? 20 : 50,
  );
  const [events, setEvents] = useState<CandidateEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { isUnseen, markSeen } = useSeenEvents();

  const setFilters = (next: FilterState) =>
    setParams(writeFilters(next, 1), { replace: true });
  const goToPage = (next: number) => setParams(writeFilters(filters, next));

  // The server filters, orders (unreviewed first, then newest) and pages the list
  const { status, ppeType, zone, dateFrom, dateTo, minConfidence } = filters;
  useEffect(() => {
    // An older, slower request must not overwrite the latest view
    let stale = false;
    // Short wait so dragging the confidence slider does not send a request per step
    const timer = window.setTimeout(() => {
      fetchEventsPage({
        limit: pageSize,
        offset: (page - 1) * pageSize,
        status: status === 'all' ? undefined : status,
        ppeType: ppeType === 'all' ? undefined : ppeType,
        zone: zone || undefined,
        dateFrom: dateFrom || undefined,
        dateTo: dateTo || undefined,
        minConfidence: minConfidence || undefined,
      })
        .then((data) => {
          if (stale) return;
          setEvents(data.items);
          setTotal(data.total);
          setError(null);
        })
        .catch((e) => {
          if (!stale) setError(e.message);
        })
        .finally(() => {
          if (!stale) setLoading(false);
        });
    }, 150);
    return () => {
      stale = true;
      window.clearTimeout(timer);
    };
  }, [status, ppeType, zone, dateFrom, dateTo, minConfidence, page, pageSize]);

  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  // The page ran out (e.g. events were reviewed elsewhere): step back to the last one that exists
  useEffect(() => {
    if (!loading && page > pageCount) {
      setParams(writeFilters(readFilters(params), pageCount), {
        replace: true,
      });
    }
  }, [loading, page, pageCount, params, setParams]);

  const visibleEvents = events;
  // Unseen is counted from the newest unreviewed events the layout already polls (at most 50)
  const recentPending = pending ?? [];
  const unseenCount = recentPending.filter(isUnseen).length;
  const moreThanRecent = pendingCount > recentPending.length;
  const markAllSeen = () => {
    fetchPendingEventIds()
      .then(markSeen)
      .catch((e: Error) => setError(e.message));
  };

  return (
    <div className={styles.page}>
      {error && <div className={styles.pendingBanner}>⚠ API 오류: {error}</div>}
      {loading && (
        <div className={styles.pendingBanner}>이벤트 목록을 불러오는 중...</div>
      )}
      {!loading && pendingCount > 0 && (
        <div className={styles.pendingBanner}>
          미검토 이벤트가 <strong>{pendingCount}건</strong> 있습니다. 확인 후
          검토해 주세요.
          {unseenCount > 0 && (
            <>
              {' '}
              아직 열어 보지 않은 이벤트는{' '}
              <strong>
                {unseenCount}건{moreThanRecent ? ' 이상' : ''}
              </strong>
              입니다.
              <button
                type="button"
                className={styles.markAllBtn}
                onClick={markAllSeen}
              >
                모두 확인함으로 표시
              </button>
            </>
          )}
        </div>
      )}

      <FilterBar filters={filters} onChange={setFilters} zones={zones} />

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
                    {isUnseen(event) && (
                      <span className={styles.unseenTag}>미확인</span>
                    )}
                  </td>
                  <td data-label="발생 일시">
                    {formatEventTime(
                      event.timestamp_start,
                      event.timestamp_timezone,
                    )}
                  </td>
                  <td data-label="구역">{event.zone_name}</td>
                  <td data-label="PPE 유형">
                    <span
                      className={`${styles.ppeTag} ${styles[event.ppe_type]}`}
                    >
                      {event.ppe_type === 'helmet' ? '안전모' : '안전조끼'}
                    </span>
                  </td>
                  <td data-label="지속시간" className={styles.minor}>
                    {event.duration_sec}초
                  </td>
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
      <div className={styles.footer}>
        {pageCount > 1 && (
          <nav className={styles.pager} aria-label="페이지 이동">
            <button
              type="button"
              className={styles.pageBtn}
              disabled={page <= 1}
              onClick={() => goToPage(page - 1)}
            >
              ← 이전
            </button>
            <span className={styles.pageInfo}>
              {page} / {pageCount}
            </span>
            <button
              type="button"
              className={styles.pageBtn}
              disabled={page >= pageCount}
              onClick={() => goToPage(page + 1)}
            >
              다음 →
            </button>
          </nav>
        )}
        <p className={styles.count}>
          {visibleEvents.length}건 표시 중 (전체 {total}건)
        </p>
      </div>
    </div>
  );
}
