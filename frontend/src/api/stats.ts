import { apiFetch } from './client';
import type { QuarterlyStats } from '../types';

export function fetchStats(quarter: string): Promise<QuarterlyStats> {
  return apiFetch<QuarterlyStats>(`/api/stats?quarter=${encodeURIComponent(quarter)}`);
}
