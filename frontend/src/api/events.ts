import { apiFetch } from './client';
import { cached, invalidate } from './apiCache';
import type {
  CandidateEvent,
  EventReview,
  ReviewRequest,
  EventReviewHistory,
  EventMedia,
  EventRetention,
  RedactionMode,
  ReviewAvailability,
} from '../types';

// How long a read is reused. Writes below invalidate, so a shorter wait is only for changes made elsewhere
const LIST_TTL_MS = 15_000;
const DETAIL_TTL_MS = 30_000;
const MEDIA_TTL_MS = 15_000;

// A write changed these events: drop what we hold so the next read is fresh
const afterWrite = <T>(result: Promise<T>, eventId?: string): Promise<T> =>
  result.then((value) => {
    invalidate('events:');
    invalidate(eventId ? `event:${eventId}` : 'event:');
    invalidate(eventId ? `media:${eventId}` : 'media:');
    return value;
  });

export interface EventListResponse {
  total: number;
  items: CandidateEvent[];
  offset?: number;
  limit?: number | null;
  has_more?: boolean;
}

export interface EventDetailResponse {
  event: CandidateEvent;
  review: EventReview | null;
  review_history?: EventReviewHistory[];
}

export interface EventPageQuery {
  limit: number; // 1-100
  offset?: number;
  status?: string;
  ppeType?: string;
  // Exact zone names; the server returns events from any of them
  zones?: string[];
  // YYYY-MM-DD in Korea time, both ends inclusive (#194)
  dateFrom?: string;
  dateTo?: string;
  // Review-only events have no confidence and are never excluded by this (server rule)
  minConfidence?: number;
}

const eventQuery = (q: Omit<EventPageQuery, 'limit' | 'offset'>) => {
  const p = new URLSearchParams();
  if (q.status) p.set('status', q.status);
  if (q.ppeType) p.set('ppe_type', q.ppeType);
  for (const z of q.zones ?? []) p.append('zones', z);
  if (q.dateFrom) p.set('date_from', q.dateFrom);
  if (q.dateTo) p.set('date_to', q.dateTo);
  if (q.minConfidence) p.set('min_confidence', String(q.minConfidence));
  return p;
};

// One page of events, filtered and ordered by the server (unreviewed first, then newest) (#223)
export function fetchEventsPage(q: EventPageQuery): Promise<EventListResponse> {
  const p = eventQuery(q);
  p.set('limit', String(q.limit));
  p.set('offset', String(q.offset ?? 0));
  return cached(`events:page:${p}`, LIST_TTL_MS, () =>
    apiFetch<EventListResponse>(`/api/events?${p}`),
  );
}

// Unreviewed events as shown in alerts and the home preview (no media or review details)
export type PendingEvent = Pick<
  CandidateEvent,
  | 'event_id'
  | 'camera_id'
  | 'zone_name'
  | 'ppe_type'
  | 'timestamp_start'
  | 'event_status'
  | 'ai_confidence'
  | 'review_only'
  | 'timestamp_timezone'
>;

export interface EventSummary {
  total: number;
  by_status: Record<string, number>;
  by_ppe_type: Record<string, number>;
  by_zone: Record<string, number>;
  recent_pending: PendingEvent[];
}

// Counts plus the newest unreviewed events: what the sidebar badge and the 20s poll need
export function fetchEventSummary(recentLimit = 50): Promise<EventSummary> {
  return cached(`events:summary:${recentLimit}`, LIST_TTL_MS, () =>
    apiFetch<EventSummary>(`/api/events/summary?recent_limit=${recentLimit}`),
  );
}

// Every unreviewed event id, read page by page (capped so a huge backlog cannot loop forever)
export async function fetchPendingEventIds(max = 1000): Promise<string[]> {
  const ids: string[] = [];
  for (let offset = 0; offset < max; offset += 100) {
    const page = await fetchEventsPage({
      limit: 100,
      offset,
      status: 'pending',
    });
    ids.push(...page.items.map((e) => e.event_id));
    if (!page.has_more || page.items.length === 0) break;
  }
  return ids;
}

export interface MergeRequest {
  source_event_ids: string[];
  expected_version: number;
  reason: string;
}

// Admin only. Folds source events into this representative event (#171)
export function mergeEvents(
  eventId: string,
  body: MergeRequest,
): Promise<unknown> {
  // A merge changes the representative and every source event
  return afterWrite(
    apiFetch(`/api/events/${encodeURIComponent(eventId)}/merge`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  );
}

export function fetchEvent(eventId: string): Promise<EventDetailResponse> {
  return cached(`event:${eventId}`, DETAIL_TTL_MS, () =>
    apiFetch<EventDetailResponse>(`/api/events/${eventId}`),
  );
}

export function submitReview(
  eventId: string,
  body: ReviewRequest,
): Promise<unknown> {
  return afterWrite(
    apiFetch(`/api/events/${eventId}/review`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
    eventId,
  );
}

// PUT endpoint — used to overwrite an existing review for hold / second_review_needed events
export function updateReview(
  eventId: string,
  body: ReviewRequest,
): Promise<unknown> {
  return afterWrite(
    apiFetch(`/api/events/${eventId}/review`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
    eventId,
  );
}

// `fresh` skips the stored copy; use it when polling for a processing state to change
export function fetchEventMedia(
  eventId: string,
  options: { fresh?: boolean } = {},
): Promise<{
  items: EventMedia[];
  retention: EventRetention;
  review_availability?: ReviewAvailability;
}> {
  return cached(`media:${eventId}`, options.fresh ? 0 : MEDIA_TTL_MS, () =>
    apiFetch(`/api/events/${encodeURIComponent(eventId)}/media`),
  );
}

export function reprocessMedia(
  mediaId: string,
  mode: RedactionMode = 'scrfd',
): Promise<EventMedia> {
  return afterWrite(
    apiFetch(
      `/api/media/${encodeURIComponent(mediaId)}/redact?redaction_mode=${mode}`,
      { method: 'POST' },
    ),
  );
}

export function uploadEventMedia(
  eventId: string,
  cameraId: string,
  file: File,
  mode: RedactionMode = 'scrfd',
): Promise<EventMedia> {
  const kind = file.type.startsWith('image/') ? 'image' : 'video';
  const params = new URLSearchParams({
    kind,
    role: 'reference',
    camera_id: cameraId,
    redaction_mode: mode,
  });
  return afterWrite(
    apiFetch(`/api/events/${encodeURIComponent(eventId)}/media?${params}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: file,
    }),
    eventId,
  );
}

export function saveEventRetention(
  eventId: string,
  body: {
    decision: 'retain' | 'delete';
    expected_version: number;
    consent_confirmed?: boolean;
    consent_reference?: string;
    consent_record_id?: string;
    retain_until?: string;
  },
): Promise<EventRetention> {
  return afterWrite(
    apiFetch<EventRetention>(
      `/api/events/${encodeURIComponent(eventId)}/retention`,
      { method: 'PUT', body: JSON.stringify(body) },
    ),
    eventId,
  );
}

export function retryMediaDeletion(eventId: string): Promise<EventRetention> {
  return afterWrite(
    apiFetch<EventRetention>(
      `/api/events/${encodeURIComponent(eventId)}/retention/retry`,
      { method: 'POST' },
    ),
    eventId,
  );
}
