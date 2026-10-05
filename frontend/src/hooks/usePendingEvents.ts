import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchEvents } from '../api/events';
import type { CandidateEvent } from '../types';

// ponytail: polling; swap the fetch for SSE once the backend pushes events
const POLL_MS = 20_000;

/**
 * Polls pending (unreviewed) events for the admin layout.
 * `newCount` accumulates events that appeared since the first load, for the alert toast.
 * Changing `trigger` (e.g. the route path) refreshes immediately and restarts the interval.
 */
export function usePendingEvents(trigger?: unknown) {
  const [pending, setPending] = useState<CandidateEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newCount, setNewCount] = useState(0);
  const known = useRef<Set<string> | null>(null);

  const refresh = useCallback(() => {
    // Skip while the tab is in the background; visibilitychange triggers a refresh on return
    if (document.hidden) return;
    fetchEvents()
      .then(({ items }) => {
        const list = items
          .filter((e) => e.event_status === 'pending')
          .sort((a, b) => b.timestamp_start.localeCompare(a.timestamp_start));
        const prev = known.current;
        if (prev) {
          const fresh = list.filter((e) => !prev.has(e.event_id)).length;
          if (fresh) setNewCount((n) => n + fresh);
        }
        known.current = new Set(list.map((e) => e.event_id));
        setPending(list);
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

  return { pending, error, newCount, clearNew: () => setNewCount(0) };
}
