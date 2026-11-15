import { apiFetch } from './client';

export interface TranslationContent { title: string; body: string; checklist: string[] }
export interface WorkerTranslation {
  status: 'original' | 'manual' | 'ready' | 'missing' | 'pending' | 'failed' | 'ineligible' | 'disabled';
  origin: 'ko' | 'reviewed' | 'ai';
  source_kind: 'material' | 'bundle'; source_id: string; source_version: number;
  available_languages: string[];
  request_language: string | null;
  poll_after_seconds: number | null;
}
export interface WorkerTranslationFields {
  language?: string | null;
  requested_language?: string;
  translation_fallback?: boolean;
  translation?: WorkerTranslation | null;
  original?: TranslationContent | null;
}
export const languageQuery = (language?: string) => language ? `?language=${encodeURIComponent(language)}` : '';
export const requestWorkerTranslation = (translation: WorkerTranslation, language: string) =>
  apiFetch<{ status: string; poll_after_seconds: number | null }>('/api/worker/education-translations/request', {
    method: 'POST', body: JSON.stringify({ source_kind: translation.source_kind, source_id: translation.source_id, source_version: translation.source_version, language }),
  });
