import { apiFetch } from './client';
import type { MaterialContent } from './materials';

export type TranslationSourceKind = 'material' | 'bundle';
export interface TranslationSource extends MaterialContent { version: number; status: 'draft' | 'published' | 'superseded' }
export const fetchTranslationSource = (kind: TranslationSourceKind, id: string) =>
  apiFetch<TranslationSource>(`/api/education/${kind === 'material' ? 'materials' : 'bundles'}/${encodeURIComponent(id)}`);
export interface EducationTranslation extends MaterialContent {
  translation_id: string;
  language: string;
  source_version: number;
  source_kind: TranslationSourceKind;
  status: 'draft' | 'published' | 'superseded';
  version: number;
  stale: boolean;
  provider: string;
}
const sourcePath = (kind: TranslationSourceKind, id: string) =>
  `/api/education/${kind === 'material' ? 'materials' : 'bundles'}/${encodeURIComponent(id)}/translations`;
export const listTranslations = (kind: TranslationSourceKind, id: string) =>
  apiFetch<{ items: EducationTranslation[] }>(sourcePath(kind, id) + '?limit=100');
export interface AutomaticTranslation {
  source_kind: TranslationSourceKind; source_id: string; source_version: number; language: string;
  status: 'queued' | 'dispatched' | 'ready' | 'failed' | 'unknown' | 'stale'; origin: 'ai';
  content: MaterialContent | null;
}
export const listAutomaticTranslations = (kind: TranslationSourceKind, id: string, version: number) =>
  apiFetch<{ items: AutomaticTranslation[] }>(sourcePath(kind, id).replace(/\/translations$/, '/automatic-translations') + `?source_version=${version}`);
export const createTranslation = (kind: TranslationSourceKind, id: string, language: string, sourceVersion: number, content: MaterialContent) =>
  apiFetch<EducationTranslation>(sourcePath(kind, id), { method: 'POST', body: JSON.stringify({ ...content, language, expected_source_version: sourceVersion }) });
export const updateTranslation = (id: string, version: number, content: MaterialContent) =>
  apiFetch<EducationTranslation>(`/api/education/translations/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify({ ...content, expected_version: version }) });
export const changeTranslationPublication = (id: string, version: number, publish: boolean) =>
  apiFetch<EducationTranslation>(`/api/education/translations/${encodeURIComponent(id)}/${publish ? 'publish' : 'unpublish'}`, { method: 'POST', body: JSON.stringify({ expected_version: version, ...(publish ? { approved: true } : {}) }) });
