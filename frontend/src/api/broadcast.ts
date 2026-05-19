import { apiFetch } from './client';
import type { BroadcastSettings } from '../types';

export function fetchBroadcastSettings(): Promise<BroadcastSettings> {
  return apiFetch<BroadcastSettings>('/api/broadcast/settings');
}

export function saveBroadcastSettings(settings: BroadcastSettings): Promise<BroadcastSettings> {
  return apiFetch<BroadcastSettings>('/api/broadcast/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  });
}
