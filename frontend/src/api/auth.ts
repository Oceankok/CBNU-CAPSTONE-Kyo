import { apiFetch, ApiError } from './client';
import type { Session, UserRole } from '../types';

const STORAGE_KEY = 'session';

// ponytail: dev-only mock until the backend ships POST /api/auth/login.
// Never active in production builds; set VITE_AUTH_MOCK=false to test against the real API in dev.
const MOCK_AUTH = import.meta.env.DEV && import.meta.env.VITE_AUTH_MOCK !== 'false';
const MOCK_USERS: Record<string, { display_name: string; role: UserRole }> = {
  admin01: { display_name: '관리자', role: 'admin' },
  worker01: { display_name: '작업자', role: 'worker' },
};

export function getSession(): Session | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

// Full reload to /login also drops any in-memory page state from the previous user
export function logout(): void {
  localStorage.removeItem(STORAGE_KEY);
  window.location.assign('/login');
}

export async function login(userId: string, password: string): Promise<Session> {
  let session: Session;
  if (MOCK_AUTH) {
    const user = MOCK_USERS[userId];
    if (!user || !password) throw new ApiError(401, '아이디 또는 비밀번호가 올바르지 않습니다.');
    session = { access_token: 'mock', user_id: userId, ...user };
  } else {
    session = await apiFetch<Session>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ user_id: userId, password }),
    });
  }
  localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  return session;
}

// Landing page per role
export function homePath(role: UserRole): string {
  return role === 'worker' ? '/worker' : '/';
}
