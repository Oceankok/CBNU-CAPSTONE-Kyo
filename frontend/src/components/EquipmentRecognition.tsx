import { useEffect, useState } from 'react';
import {
  analyzePhoto,
  fetchPendingRecognitions,
  fetchEvidence,
  confirmRecognition,
  dismissRecognition,
} from '../api/recognition';
import type { Recognition } from '../api/recognition';
import type { Camera } from '../api/cameras';
import type { ZonePpe } from '../types';
import PpeChips from './PpeChips';
import styles from './EquipmentRecognition.module.css';

// Codes from #124; analysis failures come back as 201 + status=failed + error_code
const ERRORS: Record<string, string> = {
  redaction_failed:
    '얼굴 가림 처리에 실패해 외부 분석을 하지 않았습니다. 서버의 비식별화 설정(GPU·모델)을 확인한 뒤 다시 분석하거나 직접 등록하세요.',
  provider_not_configured:
    '서버에 장비 인식 서비스가 설정되지 않았습니다. 직접 등록을 이용하세요.',
  provider_auth_required:
    '서버의 ChatGPT 연결이 만료되었습니다. 서버 PC에서 다시 로그인해야 합니다.',
  provider_usage_limit:
    '분석 사용량 한도에 도달했습니다. 나중에 다시 시도하거나 직접 등록하세요.',
  provider_timeout: '분석 시간이 초과되었습니다. 다시 시도해 주세요.',
  provider_rate_limited:
    '요청이 많아 잠시 제한되었습니다. 잠시 후 다시 시도해 주세요.',
  provider_unavailable:
    '분석 서비스에 연결할 수 없습니다. 잠시 후 다시 시도하거나 직접 등록하세요.',
  provider_response_invalid:
    '분석 결과를 해석하지 못했습니다. 다시 시도하거나 직접 등록하세요.',
  unsupported_image_type: 'JPG 또는 PNG 사진만 분석할 수 있습니다.',
  image_too_large: '사진이 너무 큽니다. 10MB 이하로 줄여 주세요.',
  source_invalid: '사진 파일이 손상되었거나 너무 큽니다(최대 16메가픽셀).',
  evidence_unavailable:
    '근거 이미지가 만료되었거나 없습니다. 새로 분석하거나 직접 등록하세요.',
  recognition_evidence_expired:
    '근거 이미지가 만료된 후보는 확정할 수 없습니다. 새로 분석하거나 직접 등록하세요.',
  recognition_already_confirmed:
    '이미 확정된 후보입니다. 장비 목록을 확인하세요.',
  recognition_not_confirmable: '확정할 수 없는 후보입니다 (실패 또는 기각됨).',
  candidate_not_in_recognition:
    '선택한 후보가 이 분석 결과에 없습니다. 다시 선택해 주세요.',
  zone_not_found: '등록되지 않은 구역입니다.',
};
const msg = (code: string) => ERRORS[code] ?? code;

interface Draft {
  candidate_id: string | null;
  name: string;
  equipment_type: string;
  zone_name: string;
  required_ppe: ZonePpe[];
}

export default function EquipmentRecognition({
  zones,
  cameras,
  onRegistered,
  onManual,
}: {
  zones: string[];
  cameras: Camera[];
  onRegistered: () => Promise<unknown>;
  onManual: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [cameraId, setCameraId] = useState('');
  const [busy, setBusy] = useState<'analyze' | 'save' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [rec, setRec] = useState<Recognition | null>(null);
  const [evidence, setEvidence] = useState<string | null>(null); // object URL
  const [evidenceError, setEvidenceError] = useState<string | null>(null);
  const [pending, setPending] = useState<Recognition[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);

  useEffect(() => {
    fetchPendingRecognitions()
      .then(setPending)
      .catch(() => {}); // the list is a convenience; analysis still works without it
  }, []);

  // Load the redacted evidence image with Bearer auth; revoke the object URL when it changes
  useEffect(() => {
    if (!rec || rec.status !== 'analyzed') return;
    let url: string | null = null;
    fetchEvidence(rec)
      .then((blob) => {
        url = URL.createObjectURL(blob);
        setEvidence(url);
      })
      .catch((e: Error) => setEvidenceError(msg(e.message)));
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [rec]);

  const zoneOf = (camera: string | null) =>
    cameras.find((c) => c.camera_id === camera)?.zone_name ?? '';

  const open = (r: Recognition) => {
    setRec(r);
    setEvidence(null);
    setEvidenceError(null);
    setError(
      r.status === 'failed'
        ? msg(r.error_code ?? 'provider_unavailable')
        : null,
    );
    const first = r.candidates[0];
    setDraft(
      r.status === 'analyzed'
        ? {
            candidate_id: first?.candidate_id ?? null,
            name: first?.label ?? '',
            equipment_type: first?.equipment_type ?? '',
            zone_name: zoneOf(r.camera_id) || zones[0] || '',
            required_ppe: [],
          }
        : null,
    );
  };

  const close = () => {
    setRec(null);
    setDraft(null);
    setEvidence(null);
  };

  const analyze = () => {
    if (!file) return;
    setBusy('analyze');
    setError(null);
    setNotice(null);
    analyzePhoto(file, cameraId || null)
      .then((r) => {
        open(r);
        if (r.status === 'analyzed') setPending((p) => [r, ...p]);
      })
      .catch((e: Error) => setError(msg(e.message)))
      .finally(() => setBusy(null));
  };

  const pick = (candidateId: string | null) => {
    const c = rec?.candidates.find((x) => x.candidate_id === candidateId);
    setDraft(
      (d) =>
        d && {
          ...d,
          candidate_id: candidateId,
          ...(c
            ? { name: c.label, equipment_type: c.equipment_type ?? '' }
            : {}),
        },
    );
  };

  const confirm = () => {
    if (!rec || !draft) return;
    setBusy('save');
    setError(null);
    confirmRecognition(rec.recognition_id, {
      candidate_id: draft.candidate_id,
      name: draft.name.trim(),
      equipment_type: draft.equipment_type.trim() || null,
      zone_name: draft.zone_name,
      required_ppe: draft.required_ppe,
    })
      .then(async (eq) => {
        setNotice(
          `'${eq.name}'을(를) 장비로 등록했습니다. 아래 카메라별 적용 PPE에서 카메라에 연결할 수 있습니다.`,
        );
        setPending((p) =>
          p.filter((x) => x.recognition_id !== rec.recognition_id),
        );
        close();
        setFile(null);
        await onRegistered();
      })
      .catch((e: Error) => setError(msg(e.message)))
      .finally(() => setBusy(null));
  };

  const dismiss = () => {
    if (!rec) return;
    setBusy('save');
    dismissRecognition(rec.recognition_id)
      .then(() => {
        setPending((p) =>
          p.filter((x) => x.recognition_id !== rec.recognition_id),
        );
        setNotice('후보를 사용하지 않기로 했습니다.');
        close();
      })
      .catch((e: Error) => setError(msg(e.message)))
      .finally(() => setBusy(null));
  };

  return (
    <section className={styles.card}>
      <h3 className={styles.title}>사진으로 장비 등록 (AI 후보)</h3>
      <p className={styles.hint}>
        장비 사진을 올리면 얼굴을 가린 뒤 AI가 장비 종류 후보를 제시합니다.
        이름·구역·추가 PPE는 관리자가 확인해 확정해야 등록됩니다.
      </p>

      {notice && <p className={styles.ok}>✓ {notice}</p>}
      {error && <p className={styles.error}>⚠ {error}</p>}

      {!rec && (
        <div className={styles.row}>
          <input
            type="file"
            accept="image/jpeg,image/png"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            aria-label="장비 사진"
          />
          <select
            className={styles.input}
            value={cameraId}
            onChange={(e) => setCameraId(e.target.value)}
          >
            <option value="">촬영 카메라 (선택)</option>
            {cameras.map((c) => (
              <option key={c.camera_id} value={c.camera_id}>
                {c.camera_id}
                {c.zone_name ? ` · ${c.zone_name}` : ''}
              </option>
            ))}
          </select>
          <button
            className={styles.primaryBtn}
            disabled={!file || busy === 'analyze'}
            onClick={analyze}
          >
            {busy === 'analyze' ? '분석 중… (최대 30초)' : '분석'}
          </button>
        </div>
      )}

      {rec?.status === 'failed' && (
        <div className={styles.actions}>
          <button className={styles.btn} onClick={close}>
            다른 사진으로 다시 분석
          </button>
          <button className={styles.btn} onClick={onManual}>
            직접 등록
          </button>
        </div>
      )}

      {rec?.status === 'analyzed' && draft && (
        <div className={styles.result}>
          <div className={styles.evidence}>
            {evidence ? (
              <img src={evidence} alt="얼굴을 가린 장비 사진" />
            ) : (
              <p className={styles.hint}>
                {evidenceError ?? '근거 이미지를 불러오는 중...'}
              </p>
            )}
            {rec.evidence_expires_at && (
              <p className={styles.hint}>
                근거 이미지 보관:{' '}
                {new Date(rec.evidence_expires_at).toLocaleString('ko-KR')}까지
              </p>
            )}
          </div>

          <fieldset className={styles.candidates}>
            <legend className={styles.label}>
              AI 후보{' '}
              {rec.candidates.length ? '' : '— 후보 없음, 직접 입력하세요'}
            </legend>
            {rec.candidates.map((c) => (
              <label key={c.candidate_id} className={styles.candidate}>
                <input
                  type="radio"
                  name="candidate"
                  checked={draft.candidate_id === c.candidate_id}
                  onChange={() => pick(c.candidate_id)}
                />
                <span>
                  <strong>{c.label}</strong>
                  {c.equipment_type && (
                    <span className={styles.hint}> ({c.equipment_type})</span>
                  )}
                  {/* provider estimate only — not a verified accuracy */}
                  {c.confidence != null &&
                    c.confidence_kind === 'provider_estimate' && (
                      <span className={styles.hint}>
                        {' '}
                        · AI 추정 {Math.round(c.confidence * 100)}%
                      </span>
                    )}
                  {c.reason && (
                    <span className={styles.reason}>{c.reason}</span>
                  )}
                </span>
              </label>
            ))}
            {rec.candidates.length > 0 && (
              <label className={styles.candidate}>
                <input
                  type="radio"
                  name="candidate"
                  checked={draft.candidate_id === null}
                  onChange={() => pick(null)}
                />
                <span>해당 없음 — 직접 입력</span>
              </label>
            )}
          </fieldset>

          <div className={styles.row}>
            <input
              className={styles.input}
              placeholder="장비 이름 (예: 지게차 1호)"
              value={draft.name}
              maxLength={100}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            />
            <input
              className={styles.input}
              placeholder="종류 (선택)"
              value={draft.equipment_type}
              maxLength={100}
              onChange={(e) =>
                setDraft({ ...draft, equipment_type: e.target.value })
              }
            />
            <select
              className={styles.input}
              value={draft.zone_name}
              onChange={(e) =>
                setDraft({ ...draft, zone_name: e.target.value })
              }
            >
              <option value="">구역 선택</option>
              {zones.map((z) => (
                <option key={z} value={z}>
                  {z}
                </option>
              ))}
            </select>
          </div>
          <p className={styles.label}>
            이 장비 앞에서 추가로 필요한 PPE (AI가 정하지 않습니다)
          </p>
          <PpeChips
            value={draft.required_ppe}
            onChange={(required_ppe) => setDraft({ ...draft, required_ppe })}
          />

          <div className={styles.actions}>
            <button
              className={styles.btn}
              disabled={busy === 'save'}
              onClick={close}
            >
              나중에
            </button>
            <button
              className={styles.dangerBtn}
              disabled={busy === 'save'}
              onClick={dismiss}
            >
              사용 안 함
            </button>
            <button
              className={styles.primaryBtn}
              disabled={
                busy === 'save' ||
                !draft.name.trim() ||
                !draft.zone_name ||
                !evidence
              }
              onClick={confirm}
            >
              확정 등록
            </button>
          </div>
        </div>
      )}

      {!rec && pending.length > 0 && (
        <details className={styles.pending}>
          <summary>확정하지 않은 후보 ({pending.length})</summary>
          <ul>
            {pending.map((r) => (
              <li key={r.recognition_id}>
                <button className={styles.linkBtn} onClick={() => open(r)}>
                  {r.candidates[0]?.label ?? '후보 없음'}
                  {r.camera_id ? ` · ${r.camera_id}` : ''} ·{' '}
                  {new Date(r.created_at).toLocaleString('ko-KR')}
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
