import { apiFetch } from './client';
import { MOCK_AUTH } from './auth';

// GET /api/cameras (#122): every registered camera with its field PC links
export interface Camera {
  camera_id: string;
  camera_location: string | null;
  zone_name: string | null;
  status: 'active' | 'inactive' | 'unknown';
  source_node_id: string | null;
  output_node_id: string | null;
}

// ponytail: in-memory mock for VITE_AUTH_MOCK=true; resets on reload
export const mockCameras: Camera[] = [
  {
    camera_id: 'CAM_001',
    camera_location: '1공장 동쪽 벽면',
    zone_name: '프레스 구역',
    status: 'active',
    source_node_id: null,
    output_node_id: null,
  },
  {
    camera_id: 'CAM_002',
    camera_location: '자재 창고 입구',
    zone_name: '자재 이동 구역',
    status: 'active',
    source_node_id: null,
    output_node_id: null,
  },
  {
    camera_id: 'CAM_003',
    camera_location: null,
    zone_name: null,
    status: 'unknown',
    source_node_id: null,
    output_node_id: null,
  },
];

export async function fetchCameras(): Promise<Camera[]> {
  if (MOCK_AUTH) return structuredClone(mockCameras);
  return (await apiFetch<{ items: Camera[] }>('/api/cameras')).items;
}
