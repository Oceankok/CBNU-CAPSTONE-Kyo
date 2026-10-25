import { useEffect, useState } from 'react';
import {
  CONSENT_ERRORS,
  fetchConsents,
  openConsentFile,
  revokeConsent,
} from '../api/consents';
import type { ConsentRecord } from '../api/consents';
import ConsentUploadForm from '../components/ConsentUploadForm';
import ConfirmButton from '../components/ConfirmButton';
import styles from './ConsentsPage.module.css';

export default function ConsentsPage() {
  const [records, setRecords] = useState<ConsentRecord[] | null>(null);
  const [site, setSite] = useState('');
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Revocation reason being typed, per record
  const [revoking, setRevoking] = useState<string | null>(null);
  const [reason, setReason] = useState('');

  const load = (filter = site) =>
    fetchConsents(filter.trim() || undefined)
      .then((r) => setRecords(r.items))
      .catch((e: Error) => setError(e.message));

  useEffect(() => {
    void fetchConsents()
      .then((r) => setRecords(r.items))
      .catch((e: Error) => setError(e.message));
  }, []);

  const fail = (e: Error) => setError(CONSENT_ERRORS[e.message] ?? e.message);

  const revoke = (r: ConsentRecord) => {
    setError(null);
    revokeConsent(r.record_id, r.version, reason.trim())
      .then(() => {
        setRevoking(null);
        setReason('');
        setNotice(
          '동의를 철회했습니다. 이 동의서로 보관 중이던 이벤트의 자료는 삭제 대상으로 전환됩니다.',
        );
        return load();
      })
      .catch(fail);
  };

  return (
    <div className={styles.page}>
      <p className={styles.desc}>
        작업자에게 받은 모델 개선용 이용 동의서(PDF)를 등록하고 관리합니다.
        동의서를 철회하면 그 동의서로 보관 중이던 이벤트의 이미지·영상이
        삭제됩니다.
      </p>

      {error && <p className={styles.errorMsg}>⚠ {error}</p>}
      {notice && <p className={styles.okMsg}>{notice}</p>}

      <section className={styles.card}>
        <div className={styles.cardHead}>
          <h3 className={styles.title}>동의서 등록</h3>
          <button
            type="button"
            className={styles.toggle}
            onClick={() => setAdding((v) => !v)}
          >
            {adding ? '닫기' : '+ 새 동의서'}
          </button>
        </div>
        {adding && (
          <ConsentUploadForm
            defaultSite={site}
            onCreated={() => {
              setAdding(false);
              setNotice('동의서를 등록했습니다.');
              void load();
            }}
          />
        )}
      </section>

      <form
        className={styles.filter}
        onSubmit={(e) => {
          e.preventDefault();
          void load();
        }}
      >
        <input
          className={styles.input}
          value={site}
          onChange={(e) => setSite(e.target.value)}
          placeholder="사업장 코드로 찾기"
        />
        <button type="submit" className={styles.toggle}>
          검색
        </button>
      </form>

      {!records && !error && <p className={styles.desc}>불러오는 중...</p>}
      {records?.length === 0 && (
        <p className={styles.desc}>등록된 동의서가 없습니다.</p>
      )}

      {records?.map((r) => (
        <section
          key={r.record_id}
          className={`${styles.card} ${r.status === 'revoked' ? styles.revoked : ''}`}
        >
          <div className={styles.cardHead}>
            <h3 className={styles.title}>
              {r.site_code} · 양식 {r.form_version}
            </h3>
            <span
              className={`${styles.badge} ${r.status === 'active' ? styles.on : styles.off}`}
            >
              {r.status === 'active' ? '유효' : '철회됨'}
            </span>
          </div>
          <p className={styles.scope}>{r.scope_description}</p>
          <p className={styles.meta}>
            {new Date(r.created_at).toLocaleString('ko-KR')} · 확인{' '}
            {r.confirmed_by} · {r.page_count}쪽 ·{' '}
            {(r.byte_size / 1024).toFixed(0)}KB
          </p>
          <p className={styles.metaMono}>{r.record_id}</p>
          {r.status === 'revoked' && (
            <p className={styles.meta}>
              철회{' '}
              {r.revoked_at && new Date(r.revoked_at).toLocaleString('ko-KR')} ·{' '}
              {r.revoked_by} · {r.revocation_reason}
            </p>
          )}
          <div className={styles.actions}>
            <button
              type="button"
              className={styles.toggle}
              onClick={() => openConsentFile(r.record_id).catch(fail)}
            >
              PDF 보기
            </button>
            {r.status === 'active' &&
              (revoking === r.record_id ? (
                <div className={styles.revokeBox}>
                  <input
                    className={styles.input}
                    value={reason}
                    maxLength={1000}
                    placeholder="철회 사유 (필수)"
                    onChange={(e) => setReason(e.target.value)}
                  />
                  <button
                    type="button"
                    className={styles.toggle}
                    onClick={() => setRevoking(null)}
                  >
                    취소
                  </button>
                  <ConfirmButton
                    className={styles.danger}
                    label="철회"
                    question="연결된 이벤트의 이미지·영상이 삭제됩니다. 복구할 수 없습니다."
                    confirmLabel="철회"
                    disabled={!reason.trim()}
                    onConfirm={() => revoke(r)}
                  />
                </div>
              ) : (
                <button
                  type="button"
                  className={styles.danger}
                  onClick={() => {
                    setRevoking(r.record_id);
                    setReason('');
                  }}
                >
                  동의 철회
                </button>
              ))}
          </div>
        </section>
      ))}
    </div>
  );
}
