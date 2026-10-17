import { apiFetch, ApiError } from './client';
import { MOCK_AUTH } from './auth';
import { mockZones } from './zones';

// GET /api/cameras (#122): every registered camera with its field PC links
export interface Camera {
  camera_id: string;
  camera_location: string | null;
  zone_name: string | null;
  process_type: string | null;
  camera_angle_type: string | null;
  // 'unknown' = old rows without a status; the API only accepts active / inactive
  status: 'active' | 'inactive' | 'unknown';
  source_node_id: string | null;
  output_node_id: string | null;
}

export type CameraFields = Pick<
  Camera,
  'camera_location' | 'zone_name' | 'process_type' | 'camera_angle_type'
>;
export type NewCamera = CameraFields & { camera_id: string };

// ponytail: in-memory mock for VITE_AUTH_MOCK=true; resets on reload
export const mockCameras: Camera[] = [
  {
    camera_id: 'CAM_001',
    camera_location: '1공장 동쪽 벽면',
    zone_name: '프레스 구역',
    process_type: null,
    camera_angle_type: null,
    status: 'active',
    source_node_id: null,
    output_node_id: null,
  },
  {
    camera_id: 'CAM_002',
    camera_location: '자재 창고 입구',
    zone_name: '자재 이동 구역',
    process_type: null,
    camera_angle_type: null,
    status: 'active',
    source_node_id: null,
    output_node_id: null,
  },
  {
    camera_id: 'CAM_003',
    camera_location: null,
    zone_name: null,
    process_type: null,
    camera_angle_type: null,
    status: 'unknown',
    source_node_id: null,
    output_node_id: null,
  },
];

export async function fetchCameras(): Promise<Camera[]> {
  if (MOCK_AUTH) return structuredClone(mockCameras);
  return (await apiFetch<{ items: Camera[] }>('/api/cameras')).items;
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  body: body === undefined ? undefined : JSON.stringify(body),
});
const path = (id: string) => `/api/cameras/${encodeURIComponent(id)}`;

const findMock = (id: string) => {
  const c = mockCameras.find((x) => x.camera_id === id);
  if (!c) throw new ApiError(404, 'camera_not_found');
  return c;
};
const checkZone = (zone: string | null) => {
  if (zone && !mockZones.some((z) => z.zone_name === zone))
    throw new ApiError(422, 'zone_not_found');
};

export async function createCamera(input: NewCamera): Promise<Camera> {
  if (MOCK_AUTH) {
    if (mockCameras.some((c) => c.camera_id === input.camera_id))
      throw new ApiError(409, 'camera_already_exists');
    checkZone(input.zone_name);
    const c: Camera = {
      ...input,
      status: 'active',
      source_node_id: null,
      output_node_id: null,
    };
    mockCameras.push(c);
    return structuredClone(c);
  }
  return apiFetch<Camera>(
    '/api/cameras',
    json('POST', { ...input, status: 'active' }),
  );
}

// Only the given fields change; zone null releases the assignment
export async function updateCamera(
  id: string,
  patch: Partial<CameraFields & { status: 'active' | 'inactive' }>,
): Promise<Camera> {
  if (MOCK_AUTH) {
    const c = findMock(id);
    if (patch.zone_name !== undefined) checkZone(patch.zone_name);
    Object.assign(c, patch);
    return structuredClone(c);
  }
  return apiFetch<Camera>(path(id), json('PATCH', patch));
}
