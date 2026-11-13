import { apiFetch, ApiError } from './client';
import { MOCK_AUTH } from './auth';
import type {
  EducationBundle,
  EducationBundleSource,
  WorkerEducationBundle,
} from '../types';
import {
  bundleCreateRequest,
  bundleGenerateRequest,
  bundlePublishRequest,
  bundleSelectionRequest,
  bundleVersionRequest,
} from './educationBundleContracts';

export interface BundlePreview {
  quarter: string;
  ppe_type: EducationBundle['ppe_type'];
  sources: EducationBundleSource[];
  snapshot: string;
  confirmed_count_sum: number;
}

export interface BundleContent {
  title: string;
  body: string;
  checklist: string[];
}

export const BUNDLE_ERRORS: Record<string, string> = {
  education_bundle_invalid_selection: '2~20개의 서로 다른 구역 추천을 선택해 주세요.',
  education_bundle_same_quarter_ppe_distinct_zones_required:
    '같은 분기·같은 PPE의 서로 다른 구역만 함께 묶을 수 있습니다.',
  education_bundle_selection_changed:
    '원본 추천이 변경되었습니다. 최신 미리보기를 확인한 뒤 다시 저장해 주세요.',
  education_bundle_version_conflict:
    '다른 관리자가 자료를 변경했습니다. 최신 자료를 확인한 뒤 다시 검토해 주세요.',
  education_bundle_invalid_status:
    '현재 자료 상태에서는 이 작업을 할 수 없습니다.',
  education_bundle_publication_conflict:
    '같은 구역 집합의 다른 게시가 먼저 완료되었습니다. 최신 자료를 확인해 주세요.',
  education_bundle_not_found: '통합 교육자료를 찾을 수 없습니다.',
  education_bundle_unsupported_generation_ppe:
    '이 PPE는 AI 초안을 지원하지 않습니다. 수동으로 작성해 주세요.',
  education_generation_busy: '다른 AI 초안 생성이 진행 중입니다. 잠시 후 다시 시도해 주세요.',
  education_generation_disabled: 'AI 초안 생성이 비활성화되어 있습니다. 수동으로 작성해 주세요.',
  education_generation_not_configured:
    'AI 초안 생성이 설정되지 않았습니다. 수동으로 작성해 주세요.',
  education_provider_rate_limited: 'AI 생성 한도에 도달했습니다. 수동으로 작성해 주세요.',
  education_provider_timeout: 'AI 생성 시간이 초과되었습니다. 수동으로 작성해 주세요.',
  education_provider_unavailable: 'AI 서비스를 사용할 수 없습니다. 수동으로 작성해 주세요.',
};

export function bundleErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return BUNDLE_ERRORS[error.message] ?? error.message;
  return error instanceof Error ? error.message : String(error);
}

export function previewEducationBundle(
  recommendationIds: string[],
): Promise<BundlePreview> {
  return apiFetch('/api/education/bundles/preview', {
    method: 'POST',
    body: JSON.stringify(bundleSelectionRequest(recommendationIds)),
  });
}

export function createEducationBundle(
  recommendationIds: string[],
  expectedSnapshot: string,
  content: BundleContent,
): Promise<EducationBundle> {
  return apiFetch('/api/education/bundles', {
    method: 'POST',
    body: JSON.stringify(bundleCreateRequest(recommendationIds, expectedSnapshot, content)),
  });
}

export function generateEducationBundle(
  recommendationIds: string[],
  expectedSnapshot: string,
): Promise<EducationBundle> {
  return apiFetch('/api/education/bundles/generate', {
    method: 'POST',
    body: JSON.stringify(bundleGenerateRequest(recommendationIds, expectedSnapshot)),
  });
}

export function fetchEducationBundles(
  quarter: string,
  limit = 100,
  offset = 0,
): Promise<{ items: EducationBundle[] }> {
  return apiFetch(
    `/api/education/bundles?quarter=${encodeURIComponent(quarter)}&limit=${limit}&offset=${offset}`,
  );
}

export function fetchEducationBundle(bundleId: string): Promise<EducationBundle> {
  return apiFetch(`/api/education/bundles/${encodeURIComponent(bundleId)}`);
}

export function updateEducationBundle(
  bundleId: string,
  content: BundleContent,
  expectedVersion: number,
): Promise<EducationBundle> {
  return apiFetch(`/api/education/bundles/${encodeURIComponent(bundleId)}`, {
    method: 'PATCH',
    body: JSON.stringify({ ...content, ...bundleVersionRequest(expectedVersion) }),
  });
}

export function publishEducationBundle(
  bundleId: string,
  expectedVersion: number,
): Promise<EducationBundle> {
  return apiFetch(
    `/api/education/bundles/${encodeURIComponent(bundleId)}/publish`,
    {
      method: 'POST',
      body: JSON.stringify(bundlePublishRequest(expectedVersion)),
    },
  );
}

export function unpublishEducationBundle(
  bundleId: string,
  expectedVersion: number,
): Promise<EducationBundle> {
  return apiFetch(
    `/api/education/bundles/${encodeURIComponent(bundleId)}/unpublish`,
    {
      method: 'POST',
      body: JSON.stringify(bundleVersionRequest(expectedVersion)),
    },
  );
}

export function fetchMyEducationBundles(): Promise<{ items: WorkerEducationBundle[] }> {
  if (MOCK_AUTH) return Promise.resolve({ items: [] });
  return apiFetch('/api/worker/education-bundles');
}
