import { apiFetch } from './client';
import type { FieldCommand, FieldNode, NodeVoices } from '../types';

const node = (id: string) => `/api/nodes/${encodeURIComponent(id)}`;
const post = (body?: unknown): RequestInit => ({
  method: 'POST',
  body: JSON.stringify(body ?? {}),
});

export async function fetchNodes(): Promise<FieldNode[]> {
  return (await apiFetch<{ items: FieldNode[] }>('/api/nodes')).items;
}

// The token is returned only once; the server keeps a hash
export function registerNode(
  node_id: string,
  name: string,
): Promise<{ node_id: string; token: string }> {
  return apiFetch('/api/nodes', post({ node_id, name }));
}

export function fetchVoices(id: string): Promise<NodeVoices> {
  return apiFetch(`${node(id)}/voices`);
}

export function refreshVoices(id: string): Promise<unknown> {
  return apiFetch(`${node(id)}/voices/refresh`, post());
}

export function selectVoice(
  id: string,
  language: string,
  voice_id: string,
): Promise<unknown> {
  return apiFetch(`${node(id)}/voice`, {
    method: 'PUT',
    body: JSON.stringify({ language, voice_id }),
  });
}

export function installLanguage(
  id: string,
  language: string,
): Promise<unknown> {
  return apiFetch(`${node(id)}/languages/install`, post({ language }));
}

export function testBroadcast(id: string, message: string): Promise<unknown> {
  return apiFetch(`${node(id)}/test-broadcast`, post({ message }));
}

export async function fetchCommands(id: string): Promise<FieldCommand[]> {
  return (await apiFetch<{ items: FieldCommand[] }>(`${node(id)}/commands`))
    .items;
}

export function linkCamera(
  camera_id: string,
  source_node_id: string,
  output_node_id: string,
): Promise<unknown> {
  return apiFetch(`/api/cameras/${encodeURIComponent(camera_id)}/node`, {
    method: 'PUT',
    body: JSON.stringify({ source_node_id, output_node_id }),
  });
}
