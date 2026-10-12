import { apiFetch, ApiError } from './client';
import { MOCK_AUTH, getSession } from './auth';
import { mockZonesOf } from './users';
import type { ZoneRule } from '../types';

// ponytail: in-memory mock for VITE_AUTH_MOCK=true until the #101 API exists; resets on reload
export const mockZones: ZoneRule[] = [
  {
    zone_name: '프레스 구역',
    required_ppe: ['helmet', 'vest'],
    rules: ['금형 교체 시 반드시 전원 차단', '프레스 작동 중 손 투입 금지'],
  },
  {
    zone_name: '자재 이동 구역',
    required_ppe: ['helmet', 'vest'],
    rules: ['지게차 통행로 보행 금지'],
  },
  {
    zone_name: '절삭 가공 구역',
    required_ppe: ['helmet', 'vest', 'goggles'],
    rules: ['칩 제거 시 맨손 사용 금지'],
  },
];

export async function fetchZones(): Promise<ZoneRule[]> {
  if (MOCK_AUTH) return structuredClone(mockZones);
  return (await apiFetch<{ items: ZoneRule[] }>('/api/zones')).items;
}

export async function saveZone(rule: ZoneRule): Promise<ZoneRule> {
  const body = { required_ppe: rule.required_ppe, rules: rule.rules };
  if (MOCK_AUTH) {
    const saved = { ...rule, ...body, updated_at: new Date().toISOString() };
    const i = mockZones.findIndex((z) => z.zone_name === rule.zone_name);
    if (i >= 0) mockZones[i] = saved;
    else mockZones.push(saved);
    return structuredClone(saved);
  }
  return apiFetch<ZoneRule>(
    `/api/zones/${encodeURIComponent(rule.zone_name)}`,
    {
      method: 'PUT',
      body: JSON.stringify(body),
    },
  );
}

export async function deleteZone(zoneName: string): Promise<void> {
  if (MOCK_AUTH) {
    const i = mockZones.findIndex((z) => z.zone_name === zoneName);
    if (i >= 0) mockZones.splice(i, 1);
    return;
  }
  try {
    await apiFetch(`/api/zones/${encodeURIComponent(zoneName)}`, {
      method: 'DELETE',
    });
  } catch (e) {
    // Backend detail is English; this case is common enough to explain in Korean
    if (e instanceof ApiError && e.status === 409) {
      throw new ApiError(
        409,
        // zone_has_equipment (#122) also blocks on inactive equipment
        e.message === 'zone_has_equipment'
          ? '이 구역에 등록된 장비가 있어 삭제할 수 없습니다. 장비 관리에서 장비를 다른 구역으로 옮기세요.'
          : '이 구역에 배정된 작업자나 설치된 카메라가 있어 삭제할 수 없습니다. 작업자는 구역 카드에서 제외하세요.',
      );
    }
    throw e;
  }
}

// Rules for every zone the logged-in worker is assigned to (empty when unassigned)
export async function fetchMyZones(): Promise<ZoneRule[]> {
  if (MOCK_AUTH) {
    const session = getSession();
    const names =
      session?.role === 'worker' ? mockZonesOf(session.user_id) : [];
    return structuredClone(
      mockZones.filter((z) => names.includes(z.zone_name)),
    );
  }
  return (await apiFetch<{ items: ZoneRule[] }>('/api/worker/zones')).items;
}
