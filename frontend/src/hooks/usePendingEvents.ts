import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchEventSummary } from '../api/events';
import type { PendingEvent } from '../api/events';
import { countNew, snapshot } from '../pendingAlerts';
import type { Sample } from '../pendingAlerts';

// ponytail: polling; swap the fetch for SSE once the backend pushes events
const POLL_MS = 20_000;
// How many of the newest unreviewed events the summary returns (a sample, not the full list)
const RECENT_LIMIT = 50;

/**
 * Polls the event summary for the admin layout: the unreviewed count, the newest unreviewed
 * events and the zone list. It no longer downloads the whole event list (#223).
 * `newCount` accumulates events that newly arrived since the first load, for the alert toast.
 * The server has no exact "new since" API, so this is judged from the sample (see pendingAlerts.ts).
 * Changing `trigger` (e.g. the route path) refreshes immediately and restarts the interval.
 */
export function usePendingEvents(trigger?: unknown) {
  const [pending, setPending] = useState<PendingEvent[] | null>(null);
  const [pendingCount, setPendingCount] = useState(0);
  const [zones, setZones] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [newCount, setNewCount] = useState(0);
  const known = useRef<Sample | null>(null);

  const refresh = useCallback(() => {
    // Skip while the tab is in the background; visibilitychange triggers a refresh on return
    if (document.hidden) return;
    fetchEventSummary(RECENT_LIMIT)
      .then((summary) => {
        const list = summary.recent_pending;
        const prev = known.current;
        if (prev) {
          // An older event sliding into the sample after a review is not a new event (#240)
          const fresh = countNew(prev, list);
          if (fresh) setNewCount((n) => n + fresh);
        }
        known.current = snapshot(list, RECENT_LIMIT);
        setPending(list);
        setPendingCount(summary.by_status.pending ?? 0);
        setZones(Object.keys(summary.by_zone).filter(Boolean).sort());
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, POLL_MS);
    document.addEventListener('visibilitychange', refresh);
    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', refresh);
    };
  }, [refresh, trigger]);

  return {
    pending,
    pendingCount,
    zones,
    error,
    newCount,
    clearNew: () => setNewCount(0),
  };
}
