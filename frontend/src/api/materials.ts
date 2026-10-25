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
};

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
