import { useCallback, useEffect, useState } from 'react';
import {
  fetchEventMedia,
  reprocessMedia,
  retryMediaDeletion,
  saveEventRetention,
  uploadEventMedia,
} from '../api/events';
import { mediaUrl } from '../api/client';
import type { EventMedia, EventRetention, RedactionMode } from '../types';
import styles from './EventMediaPanel.module.css';

const STATUS: Record<EventMedia['status'], string> = {
  registered: '비식별 처리 필요',
  processing: '얼굴 가림 처리 중',
  ready: '검토 가능',
  failed: '처리 실패',
  missing: '원본 없음',
  delete_pending: '삭제 진행 중',
  deleted: '삭제됨',
};
const ERRORS: Record<string, string> = {
  scrfd_model_missing:
    '서버에 SCRFD 모델이 설치되지 않았습니다. 관리자에게 모델 설치를 요청하거나 YuNet 방식을 선택해 주세요.',
  scrfd_manifest_missing:
    'SCRFD 모델 등록 정보가 없습니다. 개발 안내 문서의 모델 등록 절차를 확인해 주세요.',
  scrfd_model_incompatible:
    '지원하는 SCRFD 10G ONNX 형식이 아닙니다. 모델 파일을 확인해 주세요.',
  gpu_runtime_missing:
    'GPU 처리에 필요한 onnxruntime-gpu가 설치되지 않았습니다. 개발 안내 문서를 확인해 주세요.',
  gpu_provider_unavailable:
    'CUDA 추론을 사용할 수 없습니다. GPU 런타임과 CUDA/cuDNN 구성을 확인해 주세요.',
  gpu_runtime_initialization_failed:
    'GPU 모델 초기화에 실패했습니다. 서버 로그와 CUDA/cuDNN 버전을 확인해 주세요.',
  gpu_inference_failed: 'GPU 추론에 실패했습니다. 서버 로그를 확인해 주세요.',
  face_model_missing: '얼굴 처리 모델이 준비되지 않았습니다.',
  face_model_checksum_mismatch: '얼굴 처리 모델 파일을 확인해 주세요.',
  video_too_long: '짧은 이벤트 클립을 사용해 주세요.',
  final_review_required: '최종 검토를 완료한 뒤 보관 여부를 결정해 주세요.',
  retention_version_conflict:
    '다른 관리자가 설정을 변경했습니다. 새로고침 후 다시 선택해 주세요.',
  file_delete_failed:
    '파일 삭제에 실패했습니다. 재시도하거나 서버의 파일 상태를 확인해 주세요.',
};
const MODE_LABEL: Record<string, string> = {
  scrfd: 'SCRFD · GPU',
  enhanced: 'YuNet · GPU',
  legacy: '기존 CPU 처리',
};

// Representative image / clip for one camera; prefers thumbnails, then anything viewable
function cameraMedia(items: EventMedia[], camera: string) {
  const own = items.filter((m) => m.camera_id === camera);
  const viewable = own.filter((m) => m.url);
  const image =
    viewable.find((m) => m.kind === 'image' && m.role === 'thumbnail') ??
    viewable.find((m) => m.kind === 'image');
  const video =
    viewable.find((m) => m.kind === 'video' && m.role === 'clip') ??
    viewable.find((m) => m.kind === 'video');
  // Most relevant non-viewable state, shown when nothing can be displayed yet
  const pending =
    own.find((m) => m.status === 'processing') ??
    own.find((m) => m.status === 'failed') ??
    own[0];
  return { image, video, pending };
}

/**
 * Event media for reviewers: redacted image/clip first, retention decision below,
 * processing details and dev tools collapsed. Parent should key this by event id.
 */
export default function EventMediaPanel({
  eventId,
  cameraId,
  finalReview,
  onChange,
}: {
  eventId: string;
  cameraId: string;
  finalReview: boolean;
  onChange: () => Promise<void>;
}) {
  const [items, setItems] = useState<EventMedia[]>([]);
  const [retention, setRetention] = useState<EventRetention | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [camera, setCamera] = useState(cameraId);
  const [playing, setPlaying] = useState(false);
  const [consent, setConsent] = useState(false);
  const [reference, setReference] = useState('');
  const [expiry, setExpiry] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [sourceCamera, setSourceCamera] = useState(cameraId);
  const [redactionMode, setRedactionMode] = useState<RedactionMode>('scrfd');

  const apply = (data: Awaited<ReturnType<typeof fetchEventMedia>>) => {
    setItems(data.items);
    setRetention(data.retention);
    setLoaded(true);
  };
  const reload = useCallback(() => fetchEventMedia(eventId).then(apply), [eventId]);

  useEffect(() => {
    fetchEventMedia(eventId)
      .then(apply)
      .catch((e: Error) => setError(e.message));
  }, [eventId]);

  // Poll while the server is still redacting or deleting
  const processing = items.some(
    (m) => m.status === 'processing' || m.status === 'delete_pending',
  );
  useEffect(() => {
    if (!processing) return;
    const timer = window.setInterval(
      () => reload().catch((e: Error) => setError(e.message)),
      5000,
    );
    return () => window.clearInterval(timer);
  }, [processing, reload]);

  useEffect(() => {
    if (!playing) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setPlaying(false);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [playing]);

  async function act(operation: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await operation();
      await reload();
      await onChange();
    } catch (e) {
      const message = e instanceof Error ? e.message : '요청에 실패했습니다.';
      setError(ERRORS[message] ?? message);
    } finally {
      setBusy(false);
    }
  }

  function decide(decision: 'retain' | 'delete') {
    if (!retention) return;
    if (
      decision === 'delete' &&
      !window.confirm(
        '이 이벤트에 연결된 모든 이미지·영상을 삭제합니다. 복구할 수 없습니다. 계속할까요?',
      )
    )
      return;
    void act(() =>
      saveEventRetention(eventId, {
        decision,
        expected_version: retention.version,
        consent_confirmed: decision === 'retain' && consent,
        consent_reference: decision === 'retain' ? reference : '',
        ...(decision === 'retain' && expiry
          ? { retain_until: new Date(expiry).toISOString() }
          : {}),
      }),
    );
  }

  const cameras = [...new Set([cameraId, ...items.map((m) => m.camera_id)])];
  const { image, video, pending } = cameraMedia(items, camera);
  const anyViewable = items.some((m) => m.url);

  return (
    <>
      {/* ─── Primary: redacted media ─────────────────────── */}
      <div className={styles.viewer}>
        {image ? (
          <img
            src={mediaUrl(image.url)}
            alt={`${camera} 이벤트 이미지 (얼굴 가림 처리)`}
          />
        ) : video ? (
          // No still image: the clip's first frame stands in as the preview
          <video
            src={mediaUrl(video.url)}
            preload="metadata"
            muted
            playsInline
          />
        ) : (
          <div className={styles.placeholder}>
            <span className={styles.placeholderIcon}>
              {pending?.status === 'processing' ? '⏳' : '📷'}
            </span>
            <p>
              {!loaded
                ? '불러오는 중...'
                : retention?.decision === 'delete'
                  ? '자료가 삭제되었습니다'
                  : pending
                    ? STATUS[pending.status]
                    : '연결된 이미지·영상이 없습니다'}
            </p>
          </div>
        )}
        {video && (
          <button className={styles.playBtn} onClick={() => setPlaying(true)}>
            ▶ 클립 재생
          </button>
        )}
      </div>

      {cameras.length > 1 && (
        <div className={styles.cameraTabs} role="tablist" aria-label="카메라">
          {cameras.map((c) => (
            <button
              key={c}
              role="tab"
              aria-selected={c === camera}
              className={`${styles.cameraTab} ${c === camera ? styles.cameraTabOn : ''}`}
              onClick={() => setCamera(c)}
            >
              {c}
            </button>
          ))}
        </div>
      )}

      {anyViewable && (
        <p className={styles.notice}>
          얼굴 가림이 일부 누락될 수 있습니다. 화면 전체를 확인해 주세요.
        </p>
      )}
      {error && (
        <p role="alert" className={styles.error}>
          ⚠ {error}
        </p>
      )}

      {playing && video && (
        <div
          className={styles.overlay}
          onClick={() => setPlaying(false)}
          role="dialog"
          aria-modal="true"
        >
          <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
            <button
              className={styles.closeBtn}
              onClick={() => setPlaying(false)}
              aria-label="닫기"
            >
              ✕
            </button>
            <video
              src={mediaUrl(video.url)}
              controls
              autoPlay
              playsInline
              className={styles.player}
            />
          </div>
        </div>
      )}

      {/* ─── Retention decision (after final review) ───────── */}
      <section className={styles.card}>
        <h4 className={styles.cardTitle}>자료 보관</h4>
        {retention?.decision === 'delete' ? (
          <>
            <p className={styles.text}>
              {retention.deleted_at
                ? '파일 삭제가 완료되었습니다. 이벤트와 검토 기록은 유지됩니다.'
                : '파일 삭제를 진행 중입니다. 자료 접근은 차단되었습니다.'}
            </p>
            {!retention.deleted_at && (
              <button
                className={styles.btn}
                disabled={busy}
                onClick={() => void act(() => retryMediaDeletion(eventId))}
              >
                삭제 재시도
              </button>
            )}
          </>
        ) : !finalReview ? (
          <p className={styles.muted}>
            최종 검토(확정·오탐)를 마친 뒤 보관 여부를 선택합니다.
          </p>
        ) : (
          <>
            {retention?.decision === 'retain' && (
              <p className={styles.text}>
                모델 개선용으로 보관 중 · 근거: {retention.consent_reference}
              </p>
            )}
            <label className={styles.check}>
              <input
                type="checkbox"
                checked={consent}
                disabled={busy}
                onChange={(e) => setConsent(e.target.checked)}
              />
              모델 개선용 이용 동의를 확인했습니다.
            </label>
            {consent && (
              <>
                <label className={styles.field}>
                  동의 확인 근거 / 기록 번호
                  <input
                    className={styles.input}
                    value={reference}
                    maxLength={500}
                    disabled={busy}
                    onChange={(e) => setReference(e.target.value)}
                  />
                </label>
                <label className={styles.field}>
                  보관 종료 시각 (선택)
                  <input
                    className={styles.input}
                    type="datetime-local"
                    value={expiry}
                    disabled={busy}
                    onChange={(e) => setExpiry(e.target.value)}
                  />
                </label>
              </>
            )}
            <div className={styles.actions}>
              <button
                className={styles.dangerBtn}
                disabled={busy || !retention}
                onClick={() => decide('delete')}
              >
                동의 없음 · 파일 삭제
              </button>
              <button
                className={styles.primaryBtn}
                disabled={busy || !retention || !consent || !reference.trim()}
                onClick={() => decide('retain')}
              >
                동의 확인 후 보관
              </button>
            </div>
          </>
        )}
      </section>

      {/* ─── Processing details / dev tools (collapsed) ────── */}
      {items.length > 0 && (
        <details className={styles.details}>
          <summary>자료 처리 정보 ({items.length}건)</summary>
          <ul className={styles.mediaList}>
            {items.map((item) => (
              <li key={item.media_id}>
                <p className={styles.text}>
                  <strong>{item.camera_id}</strong> ·{' '}
                  {item.kind === 'image' ? '이미지' : '영상'} ·{' '}
                  {STATUS[item.status]}
                </p>
                <p className={styles.muted}>
                  {MODE_LABEL[item.redaction_mode ?? ''] ?? '처리 방식 미기록'}
                  {item.processing_seconds != null &&
                    ` · ${item.processing_seconds.toFixed(2)}초`}
                  {item.inference_backend && ` · ${item.inference_backend}`}
                  {item.processed_frames != null &&
                    ` · ${item.processed_frames}프레임, 가림 영역 누적 ${item.faces_detected ?? 0}`}
                </p>
                {item.status === 'ready' && item.faces_detected === 0 && (
                  <p className={styles.muted}>
                    탐지된 얼굴이 없습니다. 누락 여부를 확인해 주세요.
                  </p>
                )}
                {item.error_code && (
                  <p className={styles.error}>
                    {ERRORS[item.error_code] ?? item.error_code}
                  </p>
                )}
                <div className={styles.actions}>
                  {item.url && (
                    <a
                      className={styles.btn}
                      href={mediaUrl(item.url)}
                      download={`${item.redaction_mode ?? 'previous'}-${item.media_id}.${item.kind === 'video' ? 'mp4' : 'jpg'}`}
                    >
                      다운로드
                    </a>
                  )}
                  {item.can_reprocess && (
                    <button
                      className={styles.btn}
                      disabled={busy}
                      onClick={() =>
                        void act(() =>
                          reprocessMedia(
                            item.media_id,
                            item.redaction_mode === 'enhanced'
                              ? 'enhanced'
                              : 'scrfd',
                          ),
                        )
                      }
                    >
                      비식별 재처리
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </details>
      )}

      {/* Manual upload / model comparison is a backend verification flow — dev server only */}
      {import.meta.env.DEV && retention?.decision !== 'delete' && (
        <details className={styles.details}>
          <summary>개발용 · 참고 자료 업로드</summary>
          <label className={styles.field}>
            비식별 처리 방식
            <select
              className={styles.input}
              disabled={busy}
              value={redactionMode}
              onChange={(e) =>
                setRedactionMode(e.target.value as RedactionMode)
              }
            >
              <option value="scrfd">SCRFD · 기본</option>
              <option value="enhanced">YuNet · 대체 방식</option>
            </select>
          </label>
          <label className={styles.field}>
            촬영 카메라 ID
            <input
              className={styles.input}
              value={sourceCamera}
              onChange={(e) => setSourceCamera(e.target.value)}
            />
          </label>
          <input
            type="file"
            accept="image/jpeg,image/png,video/mp4"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          <div className={styles.actions}>
            <button
              className={styles.btn}
              disabled={busy || !file || !sourceCamera}
              onClick={() =>
                file &&
                void act(() =>
                  uploadEventMedia(eventId, sourceCamera, file, redactionMode),
                )
              }
            >
              {busy ? '처리 중…' : '업로드 후 얼굴 처리'}
            </button>
            <button
              className={styles.btn}
              disabled={busy || !file || !sourceCamera}
              onClick={() => {
                if (!file) return;
                const selectedFile = file;
                void act(async () => {
                  await uploadEventMedia(
                    eventId,
                    sourceCamera,
                    selectedFile,
                    'enhanced',
                  );
                  await reload();
                  await uploadEventMedia(
                    eventId,
                    sourceCamera,
                    selectedFile,
                    'scrfd',
                  );
                });
              }}
            >
              YuNet·SCRFD 비교
            </button>
          </div>
        </details>
      )}
    </>
  );
}
