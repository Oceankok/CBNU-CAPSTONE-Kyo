import { useState } from 'react';
import { CONSENT_ERRORS, uploadConsent } from '../api/consents';
import type { ConsentRecord } from '../api/consents';
import styles from './ConsentUploadForm.module.css';

// Registers a signed consent PDF; the admin confirms it covers the data being kept (#117)
export default function ConsentUploadForm({
  defaultSite = '',
  onCreated,
}: {
  defaultSite?: string;
  onCreated: (record: ConsentRecord) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [site, setSite] = useState(defaultSite);
  const [scope, setScope] = useState('');
  const [formVersion, setFormVersion] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ready =
    file && site.trim() && scope.trim() && formVersion.trim() && confirmed;

  const submit = () => {
    if (!file) return;
    setBusy(true);
    setError(null);
    uploadConsent(file, {
      site_code: site.trim(),
      scope_description: scope.trim(),
      form_version: formVersion.trim(),
      consent_confirmed: confirmed,
    })
      .then((record) => {
        setFile(null);
        setScope('');
        setFormVersion('');
        setConfirmed(false);
        onCreated(record);
      })
      .catch((e: Error) => setError(CONSENT_ERRORS[e.message] ?? e.message))
      .finally(() => setBusy(false));
  };

  return (
    <div className={styles.form}>
      <label className={styles.field}>
        동의서 PDF (10MB 이하)
        <input
          className={styles.input}
          type="file"
          accept="application/pdf,.pdf"
          disabled={busy}
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
      </label>
      <div className={styles.row}>
        <label className={styles.field}>
          사업장 코드
          <input
            className={styles.input}
            value={site}
            maxLength={80}
            placeholder="예: SITE_A"
            disabled={busy}
            onChange={(e) => setSite(e.target.value)}
          />
        </label>
        <label className={styles.field}>
          양식 버전
          <input
            className={styles.input}
            value={formVersion}
            maxLength={80}
            placeholder="예: 2026-01"
            disabled={busy}
            onChange={(e) => setFormVersion(e.target.value)}
          />
        </label>
      </div>
      <label className={styles.field}>
        적용 대상·범위
        <textarea
          className={styles.input}
          rows={2}
          value={scope}
          maxLength={2000}
          placeholder="예: 프레스 구역 참여자 및 해당 이벤트의 모델 개선용 이용 동의"
          disabled={busy}
          onChange={(e) => setScope(e.target.value)}
        />
      </label>
      <label className={styles.check}>
        <input
          type="checkbox"
          checked={confirmed}
          disabled={busy}
          onChange={(e) => setConfirmed(e.target.checked)}
        />
        이 PDF가 모델 개선용 이용 동의서이며 적용 범위를 직접 확인했습니다.
      </label>
      <p className={styles.note}>
        시스템은 PDF의 서명이나 내용의 적절성을 판정하지 않습니다.
      </p>
      {error && <p className={styles.error}>⚠ {error}</p>}
      <button
        type="button"
        className={styles.submit}
        disabled={busy || !ready}
        onClick={submit}
      >
        {busy ? '등록 중…' : '동의서 등록'}
      </button>
    </div>
  );
}
