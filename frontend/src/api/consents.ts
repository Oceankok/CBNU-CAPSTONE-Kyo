import { apiBlob, apiFetch } from './client';

export interface ConsentRecord {
  record_id: string;
  site_code: string;
  scope_description: string;
  form_version: string;
  sha256: string;
  byte_size: number;
  page_count: number;
  status: 'active' | 'revoked';
  confirmed_by: string;
  created_at: string;
  revoked_by: string | null;
  revoked_at: string | null;
  revocation_reason: string | null;
  version: number;
}

export interface ConsentMetadata {
  site_code: string;
  scope_description: string;
  form_version: string;
  consent_confirmed: boolean;
}

// Backend error codes (#167) → Korean guidance
export const CONSENT_ERRORS: Record<string, string> = {
  invalid_consent_pdf: 'PDF 파일이 아니거나 손상된 파일입니다.',
  encrypted_consent_pdf: '암호가 걸린 PDF는 등록할 수 없습니다.',
  empty_consent_file: '빈 파일입니다.',
  consent_file_too_large: 'PDF는 10MB 이하만 등록할 수 있습니다.',
  invalid_consent_page_count: 'PDF는 1~100쪽만 등록할 수 있습니다.',
  invalid_consent_metadata:
    '사업장 코드는 영문·숫자로 시작하고 영문·숫자·_·-만 쓸 수 있습니다.',
  consent_confirmation_required: '동의 내용을 확인했다는 체크가 필요합니다.',
  consent_record_not_found: '동의서 기록을 찾을 수 없습니다.',
  consent_record_revoked: '이미 철회된 동의서입니다.',
  consent_version_conflict:
    '다른 관리자가 먼저 변경했습니다. 새로고침 후 다시 시도하세요.',
  consent_file_unavailable:
    '동의서 파일을 찾을 수 없습니다. 서버를 확인하세요.',
  consent_file_integrity_failed:
    '동의서 파일이 변조되었을 수 있습니다. 서버를 확인하세요.',
  consent_record_required:
    '이 이벤트는 동의서 기록이 연결되어 있어 기록으로만 변경할 수 있습니다.',
  invalid_consent_retention_expiry:
    '동의서로 보관할 때는 보관 종료 시각이 필요합니다.',
  retention_expiry_must_be_future: '보관 종료 시각은 현재보다 뒤여야 합니다.',
};

export function fetchConsents(
  siteCode?: string,
): Promise<{ items: ConsentRecord[] }> {
  const q = new URLSearchParams({ limit: '100' });
  if (siteCode) q.set('site_code', siteCode);
  return apiFetch(`/api/consent-records?${q}`);
}

export function uploadConsent(
  file: File,
  metadata: ConsentMetadata,
): Promise<ConsentRecord> {
  const form = new FormData();
  form.append('metadata', JSON.stringify(metadata));
  form.append('file', file);
  return apiFetch('/api/consent-records', { method: 'POST', body: form });
}

export function revokeConsent(
  recordId: string,
  expectedVersion: number,
  reason: string,
): Promise<ConsentRecord> {
  return apiFetch(
    `/api/consent-records/${encodeURIComponent(recordId)}/revoke`,
    {
      method: 'POST',
      body: JSON.stringify({ expected_version: expectedVersion, reason }),
    },
  );
}

// The file needs the Bearer header, so fetch it as a blob and open that
export async function openConsentFile(recordId: string): Promise<void> {
  const blob = await apiBlob(
    `/api/consent-records/${encodeURIComponent(recordId)}/file`,
  );
  const url = URL.createObjectURL(blob);
  window.open(url, '_blank', 'noopener');
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
