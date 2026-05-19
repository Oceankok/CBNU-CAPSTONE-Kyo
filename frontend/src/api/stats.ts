import { apiFetch } from './client';
import type { QuarterlyStats } from '../types';

export function fetchStats(quarter: string): Promise<QuarterlyStats> {
  return apiFetch<QuarterlyStats>(`/api/stats?quarter=${encodeURIComponent(quarter)}`);
}

// Trigger backend to re-aggregate confirmed violations into quarterly_summary
export function generateStats(quarter: string): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(
    `/api/stats/generate?quarter=${encodeURIComponent(quarter)}`,
    { method: 'POST' },
  );
}
