import { useMemo, useSyncExternalStore } from 'react';
import { getSession } from './api/auth';
import type { CandidateEvent } from './types';

// "Opened" is separate from "reviewed" (#): an admin can open an event and still leave it unreviewed.
// The server has no such state, so it is remembered per user in this browser only.
const CHANGED = 'seen-events-changed';
const MAX_IDS = 5000;

const storageKey = () => `seen_events:${getSession()?.user_id ?? 'anonymous'}`;

function readRaw(): string {
  try {
    return localStorage.getItem(storageKey()) ?? '';
  } catch {
    return '';
  }
}

function parse(raw: string): string[] {
  try {
    const v = raw ? JSON.parse(raw) : [];
    return Array.isArray(v) ? v.filter((x) => typeof x === 'string') : [];
  } catch {
    return [];
  }
}

function write(ids: string[]) {
  try {
    localStorage.setItem(storageKey(), JSON.stringify(ids.slice(-MAX_IDS)));
  } catch {
    // Private mode or blocked storage: highlights just come back on reload
  }
  window.dispatchEvent(new Event(CHANGED));
}

export function markEventsSeen(eventIds: string[]) {
  const current = parse(readRaw());
  const have = new Set(current);
  const added = eventIds.filter((id) => !have.has(id));
  if (added.length) write([...current, ...added]);
}

function subscribe(onChange: () => void) {
  window.addEventListener(CHANGED, onChange);
  window.addEventListener('storage', onChange);
  return () => {
    window.removeEventListener(CHANGED, onChange);
    window.removeEventListener('storage', onChange);
  };
}

// An event is "unseen" while it waits for review and this admin has not opened it.
// Anything already decided counts as seen, so a review done on another device does not light up here.
export function useSeenEvents() {
  const raw = useSyncExternalStore(subscribe, readRaw, () => '');
  const seen = useMemo(() => new Set(parse(raw)), [raw]);
  const isUnseen = (e: Pick<CandidateEvent, 'event_id' | 'event_status'>) =>
    e.event_status === 'pending' && !seen.has(e.event_id);
  return { isUnseen, markSeen: markEventsSeen };
}
