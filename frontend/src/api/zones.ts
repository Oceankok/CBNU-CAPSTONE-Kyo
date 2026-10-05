import { apiFetch, ApiError } from './client';
import { MOCK_AUTH, getSession } from './auth';
import { mockZoneOf } from './users';
import type { ZoneRule } from '../types';

// ponytail: in-memory mock for VITE_AUTH_MOCK=true until the #101 API exists; resets on reload
const mockZones: ZoneRule[] = [
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
    required_ppe: ['helmet'],
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
  await apiFetch(`/api/zones/${encodeURIComponent(zoneName)}`, {
    method: 'DELETE',
  });
}

// Rule for the logged-in worker's assigned zone; 404 = no zone assigned or no rule yet
export async function fetchMyZone(): Promise<ZoneRule> {
  if (MOCK_AUTH) {
    const session = getSession();
    const zoneName =
      session?.role === 'worker' ? mockZoneOf(session.user_id) : null;
    const zone = zoneName && mockZones.find((z) => z.zone_name === zoneName);
    if (!zone) throw new ApiError(404, '담당 구역이 지정되지 않았습니다.');
    return structuredClone(zone);
  }
  return apiFetch<ZoneRule>('/api/worker/zone');
}
