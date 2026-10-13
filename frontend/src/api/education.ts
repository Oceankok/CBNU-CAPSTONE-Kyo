import { apiFetch } from './client';
import { MOCK_AUTH, getSession } from './auth';
import { mockZonesOf } from './users';
import type { WorkerEducation } from '../types';

export async function fetchMyEducation(): Promise<WorkerEducation> {
  if (MOCK_AUTH) {
    const zones = mockZonesOf(getSession()?.user_id ?? '');
    const groups = zones.map((zone) => ({
      quarter: '2026-Q2',
      zone_name: zone,
      items: [
        {
          recommendation_id: `MOCK_${zone}`,
          ppe_type: 'helmet',
          zone_name: zone,
          education_topic: `${zone} 안전모 착용 기준 및 착용 전 점검 절차 교육`,
          material: null,
        },
      ],
      empty_reason: null,
    }));
    if (!groups.length) {
      return {
        quarter: null,
        zone_name: null,
        items: [],
        empty_reason: 'zone_unassigned',
        groups: [],
      };
    }
    return { ...groups[0], groups };
  }
  return apiFetch<WorkerEducation>('/api/worker/education');
}
