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
}

export interface EventDetailResponse {
  event: CandidateEvent;
  review: EventReview | null;
  review_history?: EventReviewHistory[];
}

// Dates are YYYY-MM-DD in Korea time, both ends inclusive (filtered on the server, #194)
export function fetchEvents(
  dateFrom?: string,
  dateTo?: string,
): Promise<EventListResponse> {
  const q = new URLSearchParams();
  if (dateFrom) q.set('date_from', dateFrom);
  if (dateTo) q.set('date_to', dateTo);
  const qs = q.toString();
  // The same list feeds the review page, the alert poller and the merge panel; share one read
  return cached(`events:${dateFrom ?? ''}:${dateTo ?? ''}`, LIST_TTL_MS, () =>
    apiFetch<EventListResponse>(`/api/events${qs ? `?${qs}` : ''}`),
  );
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
