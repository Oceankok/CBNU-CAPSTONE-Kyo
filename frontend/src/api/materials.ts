import { apiFetch } from './client';

export interface EducationMaterial {
  material_id: string;
  recommendation_id: string;
  quarter: string;
  zone_name: string;
  ppe_type: string;
  education_topic: string;
  title: string;
  body: string;
  checklist: string[];
  status: 'draft' | 'published' | 'superseded';
  provider: string; // 'manual' unless an AI drafted it
  model: string | null;
  created_by: string;
  created_at: string;
  updated_by: string;
  updated_at: string;
  published_by: string | null;
  published_at: string | null;
  version: number;
}

export interface MaterialContent {
  title: string;
  body: string;
  checklist: string[];
}

// Backend error codes (#168) → Korean guidance
export const MATERIAL_ERRORS: Record<string, string> = {
  education_material_version_conflict:
    '다른 관리자가 먼저 변경했습니다. 새로고침 후 다시 시도하세요.',
  published_material_read_only:
    '게시된 자료는 직접 수정할 수 없습니다. 새 초안을 작성해 게시하세요.',
  education_material_not_draft: '초안만 수정하거나 게시할 수 있습니다.',
  education_recommendation_changed:
    '추천 내용이 바뀌어 이 초안은 게시할 수 없습니다. 새 초안을 작성하세요.',
  recommendation_not_found: '추천 항목을 찾을 수 없습니다.',
  education_material_not_found: '교육 자료를 찾을 수 없습니다.',
  education_material_not_published:
    '게시 중인 자료가 아닙니다. 화면을 새로고침하세요.',
  // AI draft generation (#181)
  education_generation_disabled:
    'AI 초안 생성이 꺼져 있습니다. 직접 작성해 주세요.',
  education_generation_not_configured:
    'AI 초안 생성 설정이 없습니다. 서버 관리자에게 문의하거나 직접 작성해 주세요.',
  education_provider_auth_failed:
    'AI 서비스 키 또는 접근 권한에 문제가 있습니다. 서버 관리자에게 문의하세요.',
  education_generation_busy:
    '다른 초안을 만드는 중입니다. 잠시 뒤 다시 시도하세요.',
  education_provider_rate_limited:
    'AI 서비스 사용 한도에 걸렸습니다. 잠시 뒤 다시 시도하거나 직접 작성해 주세요.',
  education_provider_timeout:
    'AI 응답이 늦어졌습니다. 직접 작성하거나 다시 시도하세요.',
  education_provider_unavailable: 'AI 서비스에 연결하지 못했습니다.',
  education_provider_response_invalid:
    'AI 응답이 올바르지 않아 저장하지 않았습니다.',
  education_generation_refused:
    'AI가 이 주제의 생성을 거절했습니다. 직접 작성해 주세요.',
  education_topic_invalid:
    '추천 주제가 비어 있거나 너무 길어 생성할 수 없습니다.',
};

export interface GenerationStatus {
  configured: boolean;
  provider: string | null;
  model: string | null;
  reason: string | null;
}

// Local setting check only; this never calls the AI service
export function fetchGenerationStatus(): Promise<GenerationStatus> {
  return apiFetch('/api/education/generation');
}

// Each call can cost money, so the UI asks first and never retries on its own
export function generateMaterial(
  recommendationId: string,
): Promise<EducationMaterial> {
  return apiFetch(
    `/api/recommendations/${encodeURIComponent(recommendationId)}/materials/generate`,
    { method: 'POST' },
  );
}

export function fetchMaterials(
  recommendationId: string,
): Promise<{ items: EducationMaterial[] }> {
  return apiFetch(
    `/api/recommendations/${encodeURIComponent(recommendationId)}/materials?limit=100`,
  );
}

export function createMaterial(
  recommendationId: string,
  body: MaterialContent,
): Promise<EducationMaterial> {
  return apiFetch(
    `/api/recommendations/${encodeURIComponent(recommendationId)}/materials`,
    { method: 'POST', body: JSON.stringify(body) },
  );
}

export function updateMaterial(
  materialId: string,
  body: MaterialContent,
  expectedVersion: number,
): Promise<EducationMaterial> {
  return apiFetch(
    `/api/education/materials/${encodeURIComponent(materialId)}`,
    {
      method: 'PATCH',
      body: JSON.stringify({ ...body, expected_version: expectedVersion }),
    },
  );
}

export function publishMaterial(
  materialId: string,
  expectedVersion: number,
): Promise<EducationMaterial> {
  return apiFetch(
    `/api/education/materials/${encodeURIComponent(materialId)}/publish`,
    {
      method: 'POST',
      body: JSON.stringify({
        expected_version: expectedVersion,
        approved: true,
      }),
    },
  );
}

// Published → draft; workers stop seeing it. The old superseded material is not restored
export function unpublishMaterial(
  materialId: string,
  expectedVersion: number,
): Promise<EducationMaterial> {
  return apiFetch(
    `/api/education/materials/${encodeURIComponent(materialId)}/unpublish`,
    {
      method: 'POST',
      body: JSON.stringify({ expected_version: expectedVersion }),
    },
  );
}
