import { apiFetch, apiBlob, ApiError } from './client';
import { MOCK_AUTH } from './auth';
import {
  mockAddEquipment,
  type Equipment,
  type EquipmentInput,
} from './equipment';

// Equipment candidates from a photo (#123 / #124). The server redacts faces before
// sending the image to the external model; an admin always confirms the values.

export interface RecognitionCandidate {
  candidate_id: string;
  label: string;
  equipment_type: string | null;
  confidence: number | null;
  confidence_kind: 'provider_estimate' | 'not_available';
  reason: string | null;
}

export interface Recognition {
  recognition_id: string;
  camera_id: string | null;
  status: 'analyzed' | 'failed' | 'confirmed' | 'dismissed';
  candidates: RecognitionCandidate[];
  evidence_url: string | null;
  evidence_expires_at: string | null;
  equipment_id: string | null;
  error_code: string | null;
  created_at: string;
}

const MAX_BYTES = 10 * 1024 * 1024;
const base = '/api/equipment/recognitions';

// ponytail: in-memory mock for VITE_AUTH_MOCK=true; the "evidence" is the uploaded file itself
const mockRecs: Recognition[] = [];
const mockImages = new Map<string, Blob>();
const findMock = (id: string) => {
  const r = mockRecs.find((x) => x.recognition_id === id);
  if (!r) throw new ApiError(404, 'recognition_not_found');
  return r;
};

// Detect JPEG/PNG from the first bytes: the browser derives file.type from the extension,
// and a PNG saved as .jpg fails the server's signature check
async function sniffImageType(
  file: File,
): Promise<'image/jpeg' | 'image/png' | null> {
  const b = new Uint8Array(await file.slice(0, 8).arrayBuffer());
  if (b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return 'image/jpeg';
  if (
    [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a].every((v, i) => b[i] === v)
  )
    return 'image/png';
  return null;
}

export async function analyzePhoto(
  file: File,
  cameraId: string | null,
): Promise<Recognition> {
  const type = await sniffImageType(file);
  if (!type) throw new ApiError(415, 'unsupported_image_type');
  if (file.size > MAX_BYTES) throw new ApiError(413, 'image_too_large');
  if (MOCK_AUTH) {
    await new Promise((r) => setTimeout(r, 1200)); // feel of a real external call
    const id = `ER_${mockRecs.length + 1}`;
    const rec: Recognition = {
      recognition_id: id,
      camera_id: cameraId,
      status: 'analyzed',
      candidates: [
        {
          candidate_id: `${id}_1`,
          label: '지게차',
          equipment_type: 'forklift',
          confidence: null,
          confidence_kind: 'not_available',
          reason: '포크와 마스트 구조가 보입니다.',
        },
        {
          candidate_id: `${id}_2`,
          label: '팔레트 랙',
          equipment_type: 'pallet_rack',
          confidence: null,
          confidence_kind: 'not_available',
          reason: '배경에 적재 선반이 있습니다.',
        },
      ],
      evidence_url: `${base}/${id}/image`,
      evidence_expires_at: new Date(Date.now() + 86_400_000).toISOString(),
      equipment_id: null,
      error_code: null,
      created_at: new Date().toISOString(),
    };
    mockRecs.unshift(rec);
    mockImages.set(id, file);
    return structuredClone(rec);
  }
  const q = cameraId ? `?camera_id=${encodeURIComponent(cameraId)}` : '';
  // Raw bytes, not FormData; the Content-Type header overrides apiFetch's JSON default
  return apiFetch<Recognition>(`${base}${q}`, {
    method: 'POST',
    headers: { 'Content-Type': type },
    body: file,
  });
}

export async function fetchPendingRecognitions(): Promise<Recognition[]> {
  if (MOCK_AUTH)
    return structuredClone(mockRecs.filter((r) => r.status === 'analyzed'));
  return (await apiFetch<{ items: Recognition[] }>(`${base}?status=analyzed`))
    .items;
}

export async function fetchEvidence(rec: Recognition): Promise<Blob> {
  if (!rec.evidence_url) throw new ApiError(404, 'evidence_unavailable');
  if (MOCK_AUTH) {
    const img = mockImages.get(rec.recognition_id);
    if (!img) throw new ApiError(404, 'evidence_unavailable');
    return img;
  }
  return apiBlob(rec.evidence_url);
}

export async function confirmRecognition(
  id: string,
  body: EquipmentInput & { candidate_id: string | null },
): Promise<Equipment> {
  if (MOCK_AUTH) {
    const r = findMock(id);
    if (r.status === 'confirmed')
      throw new ApiError(409, 'recognition_already_confirmed');
    if (r.status !== 'analyzed')
      throw new ApiError(409, 'recognition_not_confirmable');
    const e = mockAddEquipment(body);
    Object.assign(r, { status: 'confirmed', equipment_id: e.equipment_id });
    return e;
  }
  return apiFetch<Equipment>(`${base}/${encodeURIComponent(id)}/confirm`, {
    method: 'POST',
    body: JSON.stringify({ ...body, status: 'active' }),
  });
}

export async function dismissRecognition(id: string): Promise<Recognition> {
  if (MOCK_AUTH) {
    const r = findMock(id);
    if (r.status === 'confirmed')
      throw new ApiError(409, 'recognition_already_confirmed');
    r.status = 'dismissed';
    return structuredClone(r);
  }
  return apiFetch<Recognition>(`${base}/${encodeURIComponent(id)}/dismiss`, {
    method: 'POST',
  });
}
