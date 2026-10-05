import { useCallback, useEffect, useState } from 'react';
import { fetchEventMedia, reprocessMedia, retryMediaDeletion, saveEventRetention, uploadEventMedia } from '../api/events';
import { mediaUrl } from '../api/client';
import type { EventMedia, EventRetention, RedactionMode } from '../types';
import styles from './EventMediaPanel.module.css';

const STATUS: Record<EventMedia['status'], string> = {
  registered: '비식별 처리 필요', processing: '얼굴 처리 중', ready: '검토 가능',
  failed: '처리 실패', missing: '원본 없음', delete_pending: '삭제 진행/재시도 대기', deleted: '파일 삭제 완료',
};
const ERRORS: Record<string, string> = {
  scrfd_model_missing: '서버에 SCRFD 모델이 설치되지 않았습니다. 관리자에게 모델 설치를 요청하거나 YuNet 방식을 선택해 주세요.',
  scrfd_manifest_missing: 'SCRFD 모델 등록 정보가 없습니다. 개발 안내 문서의 모델 등록 절차를 확인해 주세요.',
  scrfd_model_incompatible: '지원하는 SCRFD 10G ONNX 형식이 아닙니다. 모델 파일을 확인해 주세요.',
  gpu_runtime_missing: 'GPU 처리에 필요한 onnxruntime-gpu가 설치되지 않았습니다. 개발 안내 문서를 확인해 주세요.',
  gpu_provider_unavailable: 'CUDA 추론을 사용할 수 없습니다. GPU 런타임과 CUDA/cuDNN 구성을 확인해 주세요.',
  gpu_runtime_initialization_failed: 'GPU 모델 초기화에 실패했습니다. 서버 로그와 CUDA/cuDNN 버전을 확인해 주세요.',
  gpu_inference_failed: 'GPU 추론에 실패했습니다. 서버 로그를 확인해 주세요.',
  face_model_missing: '얼굴 처리 모델이 준비되지 않았습니다.',
  face_model_checksum_mismatch: '얼굴 처리 모델 파일을 확인해 주세요.',
  video_too_long: '짧은 이벤트 클립을 사용해 주세요.',
  final_review_required: '최종 검토를 완료한 뒤 보관 여부를 결정해 주세요.',
  retention_version_conflict: '다른 관리자가 설정을 변경했습니다. 새로고침 후 다시 선택해 주세요.',
  file_delete_failed: '파일 삭제에 실패했습니다. 재시도하거나 서버의 파일 상태를 확인해 주세요.',
};

export default function EventMediaPanel({ eventId, cameraId, finalReview, onChange }: {
  eventId: string; cameraId: string; finalReview: boolean; onChange: () => Promise<void>;
}) {
  const [items, setItems] = useState<EventMedia[]>([]);
  const [retention, setRetention] = useState<EventRetention | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [consent, setConsent] = useState(false);
  const [reference, setReference] = useState('');
  const [expiry, setExpiry] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [sourceCamera, setSourceCamera] = useState(cameraId);
  const [redactionMode, setRedactionMode] = useState<RedactionMode>('scrfd');

  const reload = useCallback(async () => {
    const data = await fetchEventMedia(eventId);
    setItems(data.items);
    setRetention(data.retention);
  }, [eventId]);
  useEffect(() => {
    setSourceCamera(cameraId);
    setConsent(false); setReference(''); setExpiry(''); setFile(null);
    void reload().catch(e => setError(e.message));
  }, [reload, cameraId]);
  const processing = items.some(m => m.status === 'processing' || m.status === 'delete_pending');
  useEffect(() => {
    if (!processing) return;
    const timer = window.setInterval(() => void reload().catch(e => setError(e.message)), 5000);
    return () => window.clearInterval(timer);
  }, [processing, reload]);

  async function act(operation: () => Promise<unknown>) {
    setBusy(true); setError(null);
    try {
      await operation();
      await reload();
      await onChange();
    } catch (e) {
      const message = e instanceof Error ? e.message : '요청에 실패했습니다.';
      setError(ERRORS[message] ?? message);
    } finally { setBusy(false); }
  }

  function decide(decision: 'retain' | 'delete') {
    if (!retention) return;
    if (decision === 'delete' && !window.confirm('이 이벤트에 연결된 모든 이미지·영상을 삭제합니다. 복구할 수 없습니다. 계속할까요?')) return;
    void act(() => saveEventRetention(eventId, {
      decision, expected_version: retention.version,
      consent_confirmed: decision === 'retain' && consent,
      consent_reference: decision === 'retain' ? reference : '',
      ...(decision === 'retain' && expiry ? { retain_until: new Date(expiry).toISOString() } : {}),
    }));
  }

  return <section className={styles.panel}>
    <div className={styles.heading}>
      <h3>이벤트 자료</h3>
      <button disabled={busy} onClick={() => void act(reload)}>새로고침</button>
    </div>
    {error && <p role="alert" className={styles.error}>{error}</p>}
    {!items.length && <p>연결된 이미지·영상이 없습니다.</p>}
    {items.map(item => <article key={item.media_id} className={styles.media}>
      <p><strong>{item.camera_id}</strong> · {item.kind === 'image' ? '이미지' : '영상'} · {STATUS[item.status]}</p>
      <p>{item.redaction_mode === 'scrfd' ? 'SCRFD · GPU / 영상 가림 보완' : item.redaction_mode === 'enhanced' ? 'YuNet · GPU / 영상 가림 보완' : item.redaction_mode === 'legacy' ? '기존 CPU 처리 결과' : '기존 결과 · 처리 방식 미기록'}
        {item.processing_seconds != null && ` · 처리 ${item.processing_seconds.toFixed(2)}초`}
        {item.inference_backend && ` · ${item.inference_backend}`}
      </p>
      {item.processed_frames != null && <p>처리 프레임 {item.processed_frames} · 얼굴/유지 영역 누적 {item.faces_detected ?? 0} (인원수가 아닙니다)</p>}
      {item.url && item.kind === 'image' && <img src={mediaUrl(item.url)} alt={`${item.camera_id} 비식별 이벤트 이미지`} />}
      {item.url && item.kind === 'video' && <video src={mediaUrl(item.url)} controls preload="metadata" />}
      {item.url && <a href={mediaUrl(item.url)} download={`${item.redaction_mode ?? 'previous'}-${item.media_id}.${item.kind === 'video' ? 'mp4' : 'jpg'}`}>처리 결과 다운로드</a>}
      {item.error_code && <p className={styles.error}>{ERRORS[item.error_code] ?? item.error_code}</p>}
      {item.status === 'ready' && item.faces_detected === 0 && <p>탐지된 얼굴이 없습니다. 얼굴 처리 누락 여부를 확인해 주세요.</p>}
      {item.can_reprocess && <button disabled={busy} onClick={() => void act(() => reprocessMedia(item.media_id, item.redaction_mode === 'enhanced' ? 'enhanced' : 'scrfd'))}>기존 자료 비식별 처리</button>}
    </article>)}
    {!!items.some(m => m.url) && <p>얼굴 탐지가 누락될 수 있으므로 검토 시 이미지·영상 전체를 확인해 주세요.</p>}
    {retention?.decision !== 'delete' && <details className={styles.upload}>
      <summary>참고 자료 추가</summary>
      <label>비식별 처리 방식<select disabled={busy} value={redactionMode} onChange={e => setRedactionMode(e.target.value as RedactionMode)}>
        <option value="scrfd">SCRFD · 기본</option>
        <option value="enhanced">YuNet · 대체 방식</option>
      </select></label>
      <p>두 방식 모두 얼굴을 가립니다. 얼굴이 보이지 않는 뒷머리는 가림 대상에 포함하지 않아도 됩니다. 영상에서 얼굴이 드러나는 순간의 처리 누락과 안전모 확인 가능 여부를 확인해 주세요.</p>
      <label>촬영 카메라 ID<input value={sourceCamera} onChange={e => setSourceCamera(e.target.value)} /></label>
      <input type="file" accept="image/jpeg,image/png,video/mp4" onChange={e => setFile(e.target.files?.[0] ?? null)} />
      <button disabled={busy || !file || !sourceCamera} onClick={() => file && void act(() => uploadEventMedia(eventId, sourceCamera, file, redactionMode))}>
        {busy ? '처리 중…' : '업로드 후 얼굴 처리'}
      </button>
      <button disabled={busy || !file || !sourceCamera} onClick={() => {
        if (!file) return;
        const selectedFile = file;
        const modes: RedactionMode[] = ['enhanced', 'scrfd'];
        void act(async () => {
          await uploadEventMedia(eventId, sourceCamera, selectedFile, modes[0]);
          await reload();
          await uploadEventMedia(eventId, sourceCamera, selectedFile, modes[1]);
        });
      }}>같은 파일로 YuNet·SCRFD 비교</button>
    </details>}
    <hr />
    <h3>검토 후 자료 보관</h3>
    {retention?.decision === 'delete' ? <>
      <p>{retention.deleted_at ? '파일 삭제가 완료되었습니다. 사건과 검토 기록은 유지됩니다.' : '파일 삭제를 진행 중입니다. 자료 접근은 차단되었습니다.'}</p>
      {!retention.deleted_at && <button disabled={busy} onClick={() => void act(() => retryMediaDeletion(eventId))}>삭제 재시도</button>}
    </> : <>
      {retention?.decision === 'retain' && <p>모델 개선 후보로 보관 중 · 확인 근거: {retention.consent_reference}</p>}
      {!finalReview && <p>보류·2차 검토를 마친 뒤 최종 보관 여부를 선택하세요.</p>}
      <label className={styles.check}><input type="checkbox" checked={consent} disabled={!finalReview || busy} onChange={e => setConsent(e.target.checked)} />모델 개선용 이용 동의를 확인했습니다.</label>
      <label>동의 확인 근거/기록 번호<input value={reference} maxLength={500} disabled={!finalReview || busy} onChange={e => setReference(e.target.value)} /></label>
      <label>보관 종료 시각 (선택)<input type="datetime-local" value={expiry} disabled={!finalReview || busy} onChange={e => setExpiry(e.target.value)} /></label>
      <div className={styles.actions}>
        <button disabled={busy || !finalReview || !retention || !consent || !reference.trim()} onClick={() => decide('retain')}>동의 확인 후 보관</button>
        <button disabled={busy || !finalReview || !retention} onClick={() => decide('delete')}>동의 없음 / 철회 · 파일 삭제</button>
      </div>
    </>}
  </section>;
}
