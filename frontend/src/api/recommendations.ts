import { apiFetch } from './client';
import type { EducationRecommendationList } from '../types';

export function fetchRecommendations(quarter: string): Promise<EducationRecommendationList> {
  return apiFetch<EducationRecommendationList>(
    `/api/recommendations?quarter=${encodeURIComponent(quarter)}`,
  );
}

export function generateRecommendations(quarter: string): Promise<EducationRecommendationList> {
  return apiFetch<EducationRecommendationList>(
    `/api/recommendations/generate?quarter=${encodeURIComponent(quarter)}`,
    { method: 'POST' },
  );
}
