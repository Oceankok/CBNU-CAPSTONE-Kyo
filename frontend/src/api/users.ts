import { apiFetch, ApiError } from './client';
import { MOCK_AUTH } from './auth';
import type { AppUser, NewUser } from '../types';

type UserPatch = Partial<Pick<AppUser, 'display_name' | 'is_active'>>;

// ponytail: in-memory mock for VITE_AUTH_MOCK=true until the #104 API exists; resets on reload
const mockUsers: AppUser[] = [
  {
    user_id: 'admin01',
    display_name: '관리자',
    role: 'admin',
    zone_names: [],
    zone_name: null,
    is_active: true,
  },
  {
    user_id: 'worker01',
    display_name: '작업자',
    role: 'worker',
    zone_names: ['프레스 구역'],
    zone_name: '프레스 구역',
    is_active: true,
  },
  {
    user_id: 'worker02',
    display_name: '작업자2',
    role: 'worker',
    zone_names: ['자재 이동 구역'],
    zone_name: '자재 이동 구역',
    is_active: true,
  },
  {
    user_id: 'worker03',
    display_name: '작업자3',
    role: 'worker',
    zone_names: [],
    zone_name: null,
    is_active: true,
  },
];

// Mock-mode lookup used by the worker screens
export const mockZonesOf = (userId: string): string[] =>
  mockUsers.find((u) => u.user_id === userId)?.zone_names ?? [];

export async function fetchUsers(): Promise<AppUser[]> {
  if (MOCK_AUTH) return structuredClone(mockUsers);
  return (await apiFetch<{ items: AppUser[] }>('/api/users')).items;
}

export async function createUser(user: NewUser): Promise<AppUser> {
  if (MOCK_AUTH) {
    if (mockUsers.some((u) => u.user_id === user.user_id)) {
      throw new ApiError(409, '이미 사용 중인 아이디입니다.');
    }
    const created: AppUser = {
      user_id: user.user_id,
      display_name: user.display_name,
      role: user.role,
      zone_names: user.zone_names,
      zone_name: user.zone_names[0] ?? null,
      is_active: true,
    };
    mockUsers.push(created);
    return structuredClone(created);
  }
  try {
    return await apiFetch<AppUser>('/api/users', {
      method: 'POST',
      body: JSON.stringify(user),
    });
  } catch (e) {
    if (e instanceof ApiError && e.status === 409)
      throw new ApiError(409, '이미 사용 중인 아이디입니다.');
    throw e;
  }
}

export async function updateUser(
  userId: string,
  patch: UserPatch,
): Promise<AppUser> {
  if (MOCK_AUTH) {
    const u = mockUsers.find((x) => x.user_id === userId);
    if (!u) throw new ApiError(404, '계정을 찾을 수 없습니다.');
    Object.assign(u, patch);
    return structuredClone(u);
  }
  return apiFetch<AppUser>(`/api/users/${encodeURIComponent(userId)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  });
}

export async function resetPassword(
  userId: string,
  password: string,
): Promise<void> {
  if (MOCK_AUTH) return;
  await apiFetch(`/api/users/${encodeURIComponent(userId)}/password`, {
    method: 'POST',
    body: JSON.stringify({ password }),
  });
}

// Replace one zone's worker list; other zones' assignments stay (PUT /api/zones/{zone}/members)
export async function setZoneMembers(
  zone: string,
  userIds: string[],
): Promise<{ zone_name: string; user_ids: string[] }> {
  if (MOCK_AUTH) {
    for (const u of mockUsers) {
      if (u.role !== 'worker') continue;
      const names = u.zone_names.filter((z) => z !== zone);
      if (userIds.includes(u.user_id)) names.push(zone);
      names.sort();
      u.zone_names = names;
      u.zone_name = names[0] ?? null;
    }
    return { zone_name: zone, user_ids: userIds };
  }
  return apiFetch(`/api/zones/${encodeURIComponent(zone)}/members`, {
    method: 'PUT',
    body: JSON.stringify({ user_ids: userIds }),
  });
}
