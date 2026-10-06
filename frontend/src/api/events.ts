import { apiFetch } from './client';
import type { CandidateEvent, EventReview, ReviewRequest, EventReviewHistory, EventMedia, EventRetention, RedactionMode } from '../types';

export interface EventListResponse {
  total: number;
  items: CandidateEvent[];
}

export interface EventDetailResponse {
  event: CandidateEvent;
  review: EventReview | null;
  review_history?: EventReviewHistory[];
}

export function fetchEvents(): Promise<EventListResponse> {
  return apiFetch<EventListResponse>('/api/events');
}

export function fetchEvent(eventId: string): Promise<EventDetailResponse> {
  return apiFetch<EventDetailResponse>(`/api/events/${eventId}`);
}

export function submitReview(eventId: string, body: ReviewRequest): Promise<unknown> {
  return apiFetch(`/api/events/${eventId}/review`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

// PUT endpoint — used to overwrite an existing review for hold / second_review_needed events
export function updateReview(eventId: string, body: ReviewRequest): Promise<unknown> {
  return apiFetch(`/api/events/${eventId}/review`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
}

export function fetchEventMedia(eventId: string): Promise<{ items: EventMedia[]; retention: EventRetention }> {
  return apiFetch(`/api/events/${encodeURIComponent(eventId)}/media`);
}

export function reprocessMedia(mediaId: string, mode: RedactionMode = 'scrfd'): Promise<EventMedia> {
  return apiFetch(`/api/media/${encodeURIComponent(mediaId)}/redact?redaction_mode=${mode}`, { method: 'POST' });
}

export function uploadEventMedia(eventId: string, cameraId: string, file: File, mode: RedactionMode = 'scrfd'): Promise<EventMedia> {
  const kind = file.type.startsWith('image/') ? 'image' : 'video';
  const params = new URLSearchParams({ kind, role: 'reference', camera_id: cameraId, redaction_mode: mode });
  return apiFetch(`/api/events/${encodeURIComponent(eventId)}/media?${params}`, {
    method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: file,
  });
}

export function saveEventRetention(eventId: string, body: {
  decision: 'retain' | 'delete'; expected_version: number;
  consent_confirmed?: boolean; consent_reference?: string; retain_until?: string;
}): Promise<EventRetention> {
  return apiFetch(`/api/events/${encodeURIComponent(eventId)}/retention`, { method: 'PUT', body: JSON.stringify(body) });
}

export function retryMediaDeletion(eventId: string): Promise<EventRetention> {
  return apiFetch(`/api/events/${encodeURIComponent(eventId)}/retention/retry`, { method: 'POST' });
}
