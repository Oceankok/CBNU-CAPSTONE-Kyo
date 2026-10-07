import { apiFetch, ApiError } from './client';
import { MOCK_AUTH } from './auth';
import { mockZones } from './zones';
import type { ZonePpe } from '../types';

// Contract: docs/20261006_Equipment_API_Contract.md (#112). Types live here until the API ships.

export interface Equipment {
  equipment_id: string;
  name: string;
  equipment_type: string | null;
  zone_name: string;
  required_ppe: ZonePpe[]; // added on top of the zone baseline
  status: 'active' | 'inactive';
  created_at: string;
  updated_at: string;
}

export type EquipmentInput = Pick<
  Equipment,
  'name' | 'equipment_type' | 'zone_name' | 'required_ppe'
>;

export interface Camera {
  camera_id: string;
  camera_location: string | null;
  zone_name: string | null;
  status: 'active' | 'inactive' | 'unknown';
  source_node_id: string | null;
  output_node_id: string | null;
}

export interface CameraEquipment {
  camera_id: string;
  scope: 'camera';
  configured: boolean;
  equipment_ids: string[];
  updated_at: string | null;
}

export interface PpePolicy {
  camera_id: string;
  zone_name: string | null;
  configured: boolean;
  configuration_issues: string[];
  zone_required_ppe: ZonePpe[] | null;
  equipment_requirements: {
    equipment_id: string;
    name: string;
    required_ppe: ZonePpe[];
  }[];
  required_ppe: ZonePpe[] | null; // null when the configuration is incomplete
  detectable_ppe: ZonePpe[];
  unsupported_ppe: ZonePpe[];
}

// ponytail: in-memory mock for VITE_AUTH_MOCK=true until the #112 API exists; resets on reload
const now = () => new Date().toISOString();
let seq = 3;
const mockEquipment: Equipment[] = [
  {
    equipment_id: 'EQ_1',
    name: '프레스 1호기',
    equipment_type: 'press',
    zone_name: '프레스 구역',
    required_ppe: ['gloves'],
    status: 'active',
    created_at: now(),
    updated_at: now(),
  },
  {
    equipment_id: 'EQ_2',
    name: '지게차 A',
    equipment_type: 'forklift',
    zone_name: '자재 이동 구역',
    required_ppe: [],
    status: 'active',
    created_at: now(),
    updated_at: now(),
  },
  {
    equipment_id: 'EQ_3',
    name: 'CNC 선반',
    equipment_type: 'lathe',
    zone_name: '절삭 가공 구역',
    required_ppe: ['goggles', 'hearing_protection'],
    status: 'active',
    created_at: now(),
    updated_at: now(),
  },
];
const mockCameras: Camera[] = [
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
const mockLinks: Record<string, CameraEquipment> = {
  CAM_001: {
    camera_id: 'CAM_001',
    scope: 'camera',
    configured: true,
    equipment_ids: ['EQ_1'],
    updated_at: now(),
  },
};
const DETECTABLE: ZonePpe[] = ['helmet', 'vest'];

const findEq = (id: string) => {
  const e = mockEquipment.find((x) => x.equipment_id === id);
  if (!e) throw new ApiError(404, 'equipment_not_found');
  return e;
};
const checkZone = (zone: string) => {
  if (!mockZones.some((z) => z.zone_name === zone))
    throw new ApiError(422, 'zone_not_found');
};

function mockPolicy(cameraId: string): PpePolicy {
  const cam = mockCameras.find((c) => c.camera_id === cameraId);
  if (!cam) throw new ApiError(404, 'camera_not_found');
  const link = mockLinks[cameraId];
  const rule = mockZones.find((z) => z.zone_name === cam.zone_name);
  const issues = [
    ...(!cam.zone_name ? ['camera_zone_unassigned'] : []),
    ...(cam.zone_name && !rule ? ['zone_rule_missing'] : []),
    ...(!link?.configured ? ['equipment_scope_unconfigured'] : []),
  ];
  const linked = (link?.equipment_ids ?? [])
    .map(findEq)
    .filter((e) => e.status === 'active');
  const required = issues.length
    ? null
    : [
        ...new Set([
          ...(rule?.required_ppe ?? []),
          ...linked.flatMap((e) => e.required_ppe),
        ]),
      ];
  return {
    camera_id: cameraId,
    zone_name: cam.zone_name,
    configured: !issues.length,
    configuration_issues: issues,
    zone_required_ppe: rule?.required_ppe ?? null,
    equipment_requirements: linked.map((e) => ({
      equipment_id: e.equipment_id,
      name: e.name,
      required_ppe: e.required_ppe,
    })),
    required_ppe: required,
    detectable_ppe: DETECTABLE,
    unsupported_ppe: (required ?? []).filter((p) => !DETECTABLE.includes(p)),
  };
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  body: JSON.stringify(body),
});

export async function fetchEquipment(): Promise<Equipment[]> {
  if (MOCK_AUTH) return structuredClone(mockEquipment);
  return (await apiFetch<{ items: Equipment[] }>('/api/equipment')).items;
}

export async function createEquipment(
  input: EquipmentInput,
): Promise<Equipment> {
  if (MOCK_AUTH) {
    checkZone(input.zone_name);
    const e: Equipment = {
      ...input,
      equipment_id: `EQ_${++seq}`,
      status: 'active',
      created_at: now(),
      updated_at: now(),
    };
    mockEquipment.push(e);
    return structuredClone(e);
  }
  return apiFetch<Equipment>('/api/equipment', json('POST', input));
}

export async function updateEquipment(
  id: string,
  patch: Partial<EquipmentInput & Pick<Equipment, 'status'>>,
): Promise<Equipment> {
  if (MOCK_AUTH) {
    const e = findEq(id);
    if (patch.zone_name && patch.zone_name !== e.zone_name) {
      checkZone(patch.zone_name);
      if (Object.values(mockLinks).some((l) => l.equipment_ids.includes(id))) {
        throw new ApiError(409, 'equipment_has_camera_links');
      }
    }
    Object.assign(e, patch, { updated_at: now() });
    return structuredClone(e);
  }
  return apiFetch<Equipment>(
    `/api/equipment/${encodeURIComponent(id)}`,
    json('PATCH', patch),
  );
}

export async function fetchCameras(): Promise<Camera[]> {
  if (MOCK_AUTH) return structuredClone(mockCameras);
  return (await apiFetch<{ items: Camera[] }>('/api/cameras')).items;
}

export async function fetchCameraEquipment(
  cameraId: string,
): Promise<CameraEquipment> {
  if (MOCK_AUTH) {
    return structuredClone(
      mockLinks[cameraId] ?? {
        camera_id: cameraId,
        scope: 'camera',
        configured: false,
        equipment_ids: [],
        updated_at: null,
      },
    );
  }
  return apiFetch<CameraEquipment>(
    `/api/cameras/${encodeURIComponent(cameraId)}/equipment`,
  );
}

export async function saveCameraEquipment(
  cameraId: string,
  equipmentIds: string[],
): Promise<CameraEquipment> {
  if (MOCK_AUTH) {
    const cam = mockCameras.find((c) => c.camera_id === cameraId);
    if (!cam) throw new ApiError(404, 'camera_not_found');
    for (const id of equipmentIds) {
      const e = findEq(id);
      if (e.zone_name !== cam.zone_name)
        throw new ApiError(409, 'equipment_zone_mismatch');
      if (e.status !== 'active') throw new ApiError(409, 'equipment_inactive');
    }
    mockLinks[cameraId] = {
      camera_id: cameraId,
      scope: 'camera',
      configured: true,
      equipment_ids: equipmentIds,
      updated_at: now(),
    };
    return structuredClone(mockLinks[cameraId]);
  }
  return apiFetch<CameraEquipment>(
    `/api/cameras/${encodeURIComponent(cameraId)}/equipment`,
    json('PUT', { equipment_ids: equipmentIds }),
  );
}

export async function fetchPpePolicy(cameraId: string): Promise<PpePolicy> {
  if (MOCK_AUTH) return mockPolicy(cameraId);
  return apiFetch<PpePolicy>(
    `/api/cameras/${encodeURIComponent(cameraId)}/ppe-policy`,
  );
}
