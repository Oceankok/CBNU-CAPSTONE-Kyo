import { getSession, clearSession } from './auth';

// Empty base URL uses the same-origin /api and /storage Vite proxy.
const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = 'ApiError';
  }
}

/**
 * Converts a storage-relative path (e.g. "storage/candidate_events/thumbnails/EVT_xxx.jpg")
 * returned by the backend into a URL served by the protected /storage endpoint.
 * Returns an empty string if no path is provided.
 */
export function mediaUrl(relativePath: string | undefined | null): string {
  if (!relativePath) return '';
  // Already a full URL (shouldn't happen, but guard anyway)
  if (relativePath.startsWith('http')) return relativePath;
  // Normalise leading slash
  const normalised = relativePath.startsWith('/') ? relativePath : `/${relativePath}`;
  return `${BASE_URL}${normalised}`;
}

// Thin wrapper around fetch that throws ApiError on non-2xx responses
export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getSession()?.access_token;
  // Spread init first so caller-supplied headers merge with (not replace) the defaults
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
  });

  // Expired or invalid session — drop it and send the user back to login
  if (res.status === 401 && token && path !== '/api/auth/login') clearSession();

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, body.detail ?? res.statusText);
  }

  return res.json() as Promise<T>;
}
