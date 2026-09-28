import { apiFetch } from './client';
import type { QuarterlyStats, TrendPoint } from '../types';

export function fetchStats(quarter: string): Promise<QuarterlyStats> {
  return apiFetch<QuarterlyStats>(`/api/stats?quarter=${encodeURIComponent(quarter)}`);
}

// Compare last two quarters in the trend array and return % change in total confirmed violations.
// Returns undefined when there is only one (or zero) data point, so the SummaryCard hides the indicator.
export function calcTrend(trend: TrendPoint[]): number | undefined {
  if (trend.length < 2) return undefined;
  const prev = trend[trend.length - 2];
  const curr = trend[trend.length - 1];
  const prevTotal = prev.helmet + prev.vest;
  if (prevTotal === 0) return undefined;
  return Math.round(((curr.helmet + curr.vest - prevTotal) / prevTotal) * 100);
}

// Trigger backend to re-aggregate confirmed violations into quarterly_summary
export function generateStats(quarter: string): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(
    `/api/stats/generate?quarter=${encodeURIComponent(quarter)}`,
    { method: 'POST' },
  );
}
