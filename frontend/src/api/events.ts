import { apiFetch } from './client';
import type { CandidateEvent, EventReview, ReviewRequest } from '../types';

export interface EventListResponse {
  total: number;
  items: CandidateEvent[];
}

export interface EventDetailResponse {
  event: CandidateEvent;
  review: EventReview | null;
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
