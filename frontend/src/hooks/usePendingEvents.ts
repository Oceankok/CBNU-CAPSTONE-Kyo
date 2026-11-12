import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchEventSummary } from '../api/events';
import type { PendingEvent } from '../api/events';

// ponytail: polling; swap the fetch for SSE once the backend pushes events
const POLL_MS = 20_000;

/**
 * Polls the event summary for the admin layout: the unreviewed count, the newest unreviewed
 * events and the zone list. It no longer downloads the whole event list (#223).
 * `newCount` accumulates events that appeared since the first load, for the alert toast.
 * Changing `trigger` (e.g. the route path) refreshes immediately and restarts the interval.
 */
export function usePendingEvents(trigger?: unknown) {
  const [pending, setPending] = useState<PendingEvent[] | null>(null);
  const [pendingCount, setPendingCount] = useState(0);
  const [zones, setZones] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [newCount, setNewCount] = useState(0);
  const known = useRef<Set<string> | null>(null);

  const refresh = useCallback(() => {
    // Skip while the tab is in the background; visibilitychange triggers a refresh on return
    if (document.hidden) return;
    fetchEventSummary()
      .then((summary) => {
        const list = summary.recent_pending;
        const prev = known.current;
        if (prev) {
          // New events are the newest, so they always show up at the top of the recent list
          const fresh = list.filter((e) => !prev.has(e.event_id)).length;
          if (fresh) setNewCount((n) => n + fresh);
        }
        // Keep ids seen earlier: an event sliding out of the recent list is not new when it returns
        known.current = new Set([
          ...(prev ?? []),
          ...list.map((e) => e.event_id),
        ]);
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
