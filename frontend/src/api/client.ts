// Base URL from Vite env — defaults to localhost for local dev
const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

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
 * returned by the backend into a full URL served by the static file mount at /storage.
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
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, body.detail ?? res.statusText);
  }

  return res.json() as Promise<T>;
}
