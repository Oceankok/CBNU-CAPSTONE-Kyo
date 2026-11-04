import { apiBlob, apiFetch } from './client';
import type { Point } from '../polygon';

// Work areas (#188): polygons on a camera image where an equipment's extra PPE applies
export interface WorkArea {
  area_id: string;
  camera_id: string;
  equipment_id: string;
  name: string;
  points: Point[];
  version: number;
}

export interface WorkAreaList {
  camera_id: string;
  enabled: boolean;
  version: number; // config version, not an area version
  items: WorkArea[];
}

export interface WorkAreaPolicy {
  scope: 'camera' | 'work_area';
  configured?: boolean;
  configuration_issues?: string[];
}

export interface AreaInput {
  name: string;
  equipment_id: string;
  points: Point[];
}

export interface Snapshot {
  snapshot_id: string;
  camera_id: string;
  status: 'queued' | 'running' | 'ready' | 'failed' | 'expired';
  capture_start_at: string;
  expires_at: number; // unix seconds
  error_code: string | null;
  image_url: string | null;
}

// Backend error codes → Korean guidance
export const AREA_ERRORS: Record<string, string> = {
  work_area_version_conflict:
    '다른 관리자가 먼저 변경했습니다. 새로고침 후 다시 시도하세요.',
  work_area_not_found: '영역을 찾을 수 없습니다.',
  work_area_limit_reached: '카메라당 영역은 32개까지 만들 수 있습니다.',
  work_areas_empty: '영역이 하나도 없어 영역 모드를 켤 수 없습니다.',
  stored_work_area_invalid:
    '저장된 영역 좌표가 손상되었습니다. 영역을 지우고 다시 그려 주세요.',
  equipment_not_linked_to_camera:
    '이 카메라에 연결된 장비가 아닙니다. 장비 관리에서 먼저 연결하세요.',
  equipment_inactive: '사용 중인 장비만 고를 수 있습니다.',
  equipment_zone_mismatch: '카메라와 같은 구역의 장비만 고를 수 있습니다.',
  camera_frame_unavailable:
    '서버에 이 카메라의 최근 영상이 없습니다. 카메라가 영상을 보내고 있는지 확인하세요.',
  snapshot_capacity_reached:
    '기준 화면 요청이 몰려 있습니다. 잠시 뒤 다시 시도하세요.',
  snapshot_not_found: '기준 화면이 만료되어 삭제되었습니다. 다시 가져오세요.',
  snapshot_source_missing: '기준 화면의 원본 영상이 없습니다. 다시 가져오세요.',
  snapshot_source_too_large: '영상 조각이 너무 커서 처리하지 못했습니다.',
  snapshot_lease_expired:
    '서버 처리가 시간 안에 끝나지 않았습니다. 다시 시도하세요.',
};

// Why the saved setup is incomplete (configuration_issues from the policy API)
export function describeIssue(code: string): string {
  const [, reason = code] = code.includes(':')
    ? code.split(/:(.*)/s)
    : [null, code];
  const map: Record<string, string> = {
    work_areas_empty:
      '영역이 하나도 없습니다. 영역을 만들거나 영역 모드를 끄세요.',
    camera_inactive: '카메라가 중지되어 있습니다.',
    zone_rule_missing: '카메라의 구역 규칙이 없습니다.',
    stored_zone_rule_invalid: '구역 규칙이 손상되었습니다.',
    equipment_inactive: '영역에 연결된 장비가 사용 중이 아닙니다.',
    equipment_zone_mismatch: '영역의 장비가 카메라와 다른 구역입니다.',
    equipment_not_linked_to_camera:
      '영역의 장비가 카메라에서 연결 해제되었습니다.',
  };
  return map[reason] ?? AREA_ERRORS[reason] ?? reason;
}

const base = (cameraId: string) =>
  `/api/cameras/${encodeURIComponent(cameraId)}`;
const json = (method: string, body: unknown): RequestInit => ({
  method,
  body: JSON.stringify(body),
});
const norm = (l: WorkAreaList): WorkAreaList => ({
  ...l,
  enabled: Boolean(l.enabled),
});

export async function fetchWorkAreas(cameraId: string): Promise<WorkAreaList> {
  return norm(await apiFetch<WorkAreaList>(`${base(cameraId)}/work-areas`));
}

export function createWorkArea(
  cameraId: string,
  input: AreaInput,
): Promise<WorkArea> {
  return apiFetch(`${base(cameraId)}/work-areas`, json('POST', input));
}

export function updateWorkArea(
  cameraId: string,
  areaId: string,
  input: AreaInput,
  expectedVersion: number,
): Promise<WorkArea> {
  return apiFetch(
    `${base(cameraId)}/work-areas/${encodeURIComponent(areaId)}`,
    json('PATCH', { ...input, expected_version: expectedVersion }),
  );
}

export async function deleteWorkArea(
  cameraId: string,
  areaId: string,
  expectedVersion: number,
): Promise<WorkAreaList> {
  return norm(
    await apiFetch<WorkAreaList>(
      `${base(cameraId)}/work-areas/${encodeURIComponent(areaId)}?expected_version=${expectedVersion}`,
      { method: 'DELETE' },
    ),
  );
}

export async function setWorkAreaMode(
  cameraId: string,
  enabled: boolean,
  expectedConfigVersion: number,
): Promise<WorkAreaList> {
  return norm(
    await apiFetch<WorkAreaList>(
      `${base(cameraId)}/work-area-config`,
      json('PUT', { enabled, expected_version: expectedConfigVersion }),
    ),
  );
}

export function fetchWorkAreaPolicy(cameraId: string): Promise<WorkAreaPolicy> {
  return apiFetch(`${base(cameraId)}/work-area-policy`);
}

export function requestSnapshot(cameraId: string): Promise<Snapshot> {
  return apiFetch(`${base(cameraId)}/snapshots`, { method: 'POST' });
}

export function fetchSnapshot(
  cameraId: string,
  snapshotId: string,
): Promise<Snapshot> {
  return apiFetch(
    `${base(cameraId)}/snapshots/${encodeURIComponent(snapshotId)}`,
  );
}

// The image needs the Bearer header, so it is fetched as a blob instead of an <img src>
export function fetchSnapshotImage(imageUrl: string): Promise<Blob> {
  return apiBlob(imageUrl);
}
