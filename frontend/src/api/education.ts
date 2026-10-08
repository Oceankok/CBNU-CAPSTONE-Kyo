import { apiFetch } from './client';
import { MOCK_AUTH, getSession } from './auth';
import { mockZoneOf } from './users';
import type { WorkerEducation } from '../types';

export async function fetchMyEducation(): Promise<WorkerEducation> {
  if (MOCK_AUTH) {
    const zone = mockZoneOf(getSession()?.user_id ?? '');
    if (!zone)
      return {
        quarter: null,
        zone_name: null,
        items: [],
        empty_reason: 'zone_unassigned',
      };
    return {
      quarter: '2026-Q2',
      zone_name: zone,
      items: [
        {
          recommendation_id: 'MOCK_1',
          ppe_type: 'helmet',
          zone_name: zone,
          education_topic: `${zone} 안전모 착용 기준 및 착용 전 점검 절차 교육`,
          material: null,
        },
      ],
      empty_reason: null,
    };
  }
  return apiFetch<WorkerEducation>('/api/worker/education');
}
