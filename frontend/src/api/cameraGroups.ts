import { apiFetch } from './client';
import type { Camera } from './cameras';

// GET /api/camera-groups (#170): cameras whose views overlap the same spot
export interface CameraGroup {
  group_id: string;
  name: string;
  camera_ids: string[];
  cameras: Camera[];
  version: number;
  created_by: string;
  updated_by: string;
  updated_at: string;
}

export interface GroupFields {
  name: string;
  camera_ids: string[];
}

// Backend error codes → Korean guidance
export const GROUP_ERRORS: Record<string, string> = {
  camera_group_version_conflict:
    '다른 관리자가 먼저 변경했습니다. 새로고침 후 다시 시도하세요.',
  camera_group_not_found: '그룹을 찾을 수 없습니다.',
  duplicate_group_camera: '같은 카메라를 두 번 넣을 수 없습니다.',
  camera_not_found: '등록되지 않은 카메라가 포함되어 있습니다.',
};

export async function fetchCameraGroups(): Promise<CameraGroup[]> {
  return (
    await apiFetch<{ items: CameraGroup[] }>('/api/camera-groups?limit=100')
  ).items;
}

export function createCameraGroup(body: GroupFields): Promise<CameraGroup> {
  return apiFetch('/api/camera-groups', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function updateCameraGroup(
  id: string,
  body: GroupFields,
  expectedVersion: number,
): Promise<CameraGroup> {
  return apiFetch(`/api/camera-groups/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify({ ...body, expected_version: expectedVersion }),
  });
}

export function deleteCameraGroup(
  id: string,
  expectedVersion: number,
): Promise<unknown> {
  return apiFetch(
    `/api/camera-groups/${encodeURIComponent(id)}?expected_version=${expectedVersion}`,
    { method: 'DELETE' },
  );
}
