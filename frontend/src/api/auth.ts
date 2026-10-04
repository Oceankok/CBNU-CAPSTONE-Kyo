import { apiFetch, ApiError } from './client';
import type { Session, UserRole } from '../types';

const STORAGE_KEY = 'session';

// Real authentication is the default. Mock login is an explicit development opt-in.
export const MOCK_AUTH = import.meta.env.DEV && import.meta.env.VITE_AUTH_MOCK === 'true';
const MOCK_USERS: Record<string, { display_name: string; role: UserRole }> = {
  admin01: { display_name: '관리자', role: 'admin' },
  worker01: { display_name: '작업자', role: 'worker' },
};

export function getSession(): Session | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const session = JSON.parse(raw) as Session;
    if (!session.access_token || !session.user_id ||
        !['admin', 'worker'].includes(session.role) ||
        (!MOCK_AUTH && session.access_token === 'mock')) {
      localStorage.removeItem(STORAGE_KEY);
      return null;
    }
    return session;
  } catch {
    return null;
  }
}

// Full reload to /login also drops any in-memory page state from the previous user
export function clearSession(): void {
  localStorage.removeItem(STORAGE_KEY);
  window.location.assign('/login');
}

export async function logout(): Promise<void> {
  try {
    if (getSession() && !MOCK_AUTH) {
      await apiFetch('/api/auth/logout', { method: 'POST' });
    }
  } catch (error) {
    // Local logout still works offline; the server token expires independently.
    console.warn('서버 로그아웃에 실패했습니다. 로컬 세션을 삭제합니다.', error);
  } finally {
    clearSession();
  }
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
