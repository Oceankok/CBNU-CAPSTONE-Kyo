import { useEffect, useRef, useState } from 'react';
import { getSession, logout } from '../api/auth';
import { fetchMyZones } from '../api/zones';
import { fetchMyEducation } from '../api/education';
import { fetchMyEducationBundles } from '../api/educationBundles';
import type { EducationGroup, WorkerEducationBundle, ZoneRule } from '../types';
import { ppeInfo } from '../ppe';
import styles from './WorkerHomePage.module.css';
import { requestWorkerTranslation, type WorkerTranslationFields } from '../api/workerTranslation';
import WorkerTranslationNote from '../components/WorkerTranslationNote';

const requestKey = (item: WorkerTranslationFields) => JSON.stringify([item.translation?.source_kind, item.translation?.source_id, item.translation?.source_version, item.requested_language]);
const LANGUAGE_NAMES: Record<string, string> = { ko: '한국어 (원문)', en: 'English', vi: 'Tiếng Việt', zh: '中文', ja: '日本語', th: 'ไทย', id: 'Bahasa Indonesia', ne: 'नेपाली', uz: 'Oʻzbekcha' };
function languageName(code: string) {
  if (LANGUAGE_NAMES[code]) return LANGUAGE_NAMES[code];
  try { return new Intl.DisplayNames([code], { type: 'language' }).of(code) ?? code; } catch { return code; }
}
async function bounded<T>(request: Promise<T>): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try { return await Promise.race([request, new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error('응답 시간 초과')), 10000); })]); }
  finally { clearTimeout(timer); }
}

export default function WorkerHomePage() {
  const session = getSession();
  const [zones, setZones] = useState<ZoneRule[] | null>(null);
  const [groups, setGroups] = useState<EducationGroup[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [educationError, setEducationError] = useState<string | null>(null);
  const [bundles, setBundles] = useState<WorkerEducationBundle[] | null>(null);
  const [bundleError, setBundleError] = useState<string | null>(null);
  const preferenceKey = `education-language:${session?.user_id ?? 'anonymous'}`;
  const [language, setLanguage] = useState(() => {
    try { const value = localStorage.getItem(preferenceKey) ?? ''; return /^[a-z]{2,3}(?:-[A-Z]{2})?$/.test(value) ? value : ''; } catch { return ''; }
  });
  const attempted = useRef(new Set<string>());
  const [failedRequests, setFailedRequests] = useState<Record<string, boolean>>({});
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    fetchMyZones()
      .then(setZones)
      .catch((e: Error) =>
        setError(`규칙을 불러오지 못했습니다. (${e.message})`),
      );
  }, []);

  useEffect(() => {
    let current = true;
    const timers: ReturnType<typeof setTimeout>[] = [];
    let singleExpiry: ReturnType<typeof setTimeout>;
    let bundleExpiry: ReturnType<typeof setTimeout>;
    const process = async (items: WorkerTranslationFields[]): Promise<number | null> => {
      let delay: number | null = null;
      for (const item of items) {
        if (!current) return null;
        const meta = item.translation;
        if (!meta) continue;
        const key = requestKey(item);
        if (meta.status !== 'missing') setFailedRequests(previous => {
          if (!(key in previous)) return previous;
          const next = { ...previous }; delete next[key]; return next;
        });
        if (meta.status === 'pending') delay = Math.max(3, meta.poll_after_seconds ?? 3);
        if (meta.status !== 'missing' || !item.requested_language || item.requested_language === 'ko') continue;
        if (attempted.current.has(key)) continue;
        attempted.current.add(key);
        try {
          const queued = await bounded(requestWorkerTranslation(meta, item.requested_language));
          // Another request may have completed or published a manual translation meanwhile.
          if (['pending', 'ready', 'manual'].includes(queued.status)) delay = Math.max(3, queued.poll_after_seconds ?? 3);
        } catch {
          if (current) setFailedRequests(previous => ({ ...previous, [key]: true }));
          // Re-read the current source once; never automatically retry a rejected job.
          delay = 3;
        }
      }
      return delay;
    };
    const readSingle = async () => {
      try {
        const result = await bounded(fetchMyEducation(language || undefined));
        if (!current) return;
        setGroups(result.groups); setEducationError(null);
        clearTimeout(singleExpiry);
        singleExpiry = setTimeout(() => { if (current) { setGroups(null); setEducationError('최신 게시 상태를 확인하지 못해 교육 본문을 숨겼습니다. 다시 조회하세요.'); } }, 40000);
        const delay = await process(result.groups.flatMap(group => group.items));
        if (current) timers.push(setTimeout(() => void readSingle(), (delay ?? 30) * 1000));
      } catch (e) { if (current) { setGroups(null); setEducationError(`최신 게시 상태를 확인하지 못해 본문을 숨겼습니다. (${e instanceof Error ? e.message : String(e)})`); } }
    };
    const readBundles = async () => {
      try {
        const result = await bounded(fetchMyEducationBundles(language || undefined));
        if (!current) return;
        setBundles(result.items); setBundleError(null);
        clearTimeout(bundleExpiry);
        bundleExpiry = setTimeout(() => { if (current) { setBundles(null); setBundleError('최신 게시 상태를 확인하지 못해 본문을 숨겼습니다. 다시 조회하세요.'); } }, 40000);
        const delay = await process(result.items);
        if (current) timers.push(setTimeout(() => void readBundles(), (delay ?? 30) * 1000));
      } catch (e) { if (current) { setBundles(null); setBundleError(e instanceof Error ? e.message : String(e)); } }
    };
    void readSingle(); void readBundles();
    const foreground = () => {
      if (document.visibilityState !== 'visible') return;
      setGroups(null); setBundles(null); setRefresh(value => value + 1);
    };
    window.addEventListener('focus', foreground);
    document.addEventListener('visibilitychange', foreground);
    return () => {
      current = false; timers.forEach(clearTimeout); clearTimeout(singleExpiry); clearTimeout(bundleExpiry);
      window.removeEventListener('focus', foreground); document.removeEventListener('visibilitychange', foreground);
    };
  }, [language, refresh]);

  const availableLanguages = Array.from(new Set(['ko', 'en', 'vi', 'zh', 'ja', 'th', 'id', 'ne', 'uz', ...(language ? [language] : []),
    ...(groups ?? []).flatMap(group => group.items.flatMap(item => item.translation?.available_languages ?? [])),
    ...(bundles ?? []).flatMap(item => item.translation?.available_languages ?? []),
  ]));

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <span className={styles.name}>{session?.display_name}</span>
        <button className={styles.logout} onClick={logout}>
          로그아웃
        </button>
      </header>
      <main className={styles.content}>
        <section className={styles.card}>
          <label>교육자료 언어 <select value={language} onChange={event => {
            const next = event.target.value;
            try { if (next) localStorage.setItem(preferenceKey, next); else localStorage.removeItem(preferenceKey); } catch { /* Preference still applies in this page. */ }
            setGroups(null); setBundles(null); setLanguage(next);
            Object.keys(failedRequests).forEach(key => attempted.current.delete(key));
            setFailedRequests({});
          }}>
            <option value="">브라우저 언어 사용</option>
            {availableLanguages.map(code => <option key={code} value={code}>{languageName(code)}</option>)}
          </select></label>
          <button type="button" onClick={() => setRefresh(value => value + 1)}>교육자료 새로 조회</button>
          <p className={styles.muted}>선택 언어를 우선 사용하며, 선택하지 않으면 브라우저 언어를 사용합니다. 번역이 없으면 한국어 원문을 표시합니다. 구역 안전 수칙은 한국어로 표시합니다.</p>
          {Object.keys(failedRequests).length > 0 && <div role="status">
            <p>번역 요청을 완료하지 못했습니다. 한국어 원문은 계속 확인할 수 있습니다.</p>
            <button type="button" onClick={() => {
              Object.keys(failedRequests).forEach(key => attempted.current.delete(key));
              setFailedRequests({}); setRefresh(value => value + 1);
            }}>실패한 번역 요청 다시 시도</button>
          </div>}
          {educationError && groups && <p role="status">{educationError} 현재 표시된 자료가 최신인지 확인하지 못했습니다.</p>}
          {bundleError && bundles && <p role="status">{bundleError} 현재 표시된 자료가 최신인지 확인하지 못했습니다.</p>}
        </section>
        {!zones && <p className={styles.muted}>{error ?? '불러오는 중...'}</p>}

        {zones?.length === 0 && (
          <section className={styles.card}>
            <p className={styles.zoneLabel}>내 작업 구역</p>
            <p className={styles.muted}>
              담당 구역이 아직 지정되지 않았습니다. 관리자에게 문의하세요.
            </p>
          </section>
        )}

        {/* One card per assigned zone: its PPE, rules and education (PPE is not merged across zones) */}
        {zones?.map((zone) => {
          const edu = groups?.find((g) => g.zone_name === zone.zone_name);
          return (
            <section key={zone.zone_name} className={styles.card}>
              <p className={styles.zoneLabel}>작업 구역</p>
              <h2 className={styles.zoneName}>{zone.zone_name}</h2>

              <h3 className={styles.subTitle}>필수 착용</h3>
              {zone.required_ppe.length ? (
                <ul className={styles.ppeList}>
                  {zone.required_ppe.map((p) => (
                    <li key={p} className={styles.ppeItem}>
                      <span className={styles.ppeIcon}>{ppeInfo(p).icon}</span>
                      {ppeInfo(p).label}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className={styles.muted}>지정된 필수 PPE가 없습니다.</p>
              )}

              {zone.rules.length > 0 && (
                <>
                  <h3 className={styles.subTitle}>안전 수칙</h3>
                  <ol className={styles.rules}>
                    {zone.rules.map((r) => (
                      <li key={r}>{r}</li>
                    ))}
                  </ol>
                </>
              )}

              <h3 className={styles.subTitle}>
                안전 교육
                {edu?.quarter && (
                  <span className={styles.quarter}> · {edu.quarter}</span>
                )}
              </h3>
              {!edu ? (
                <p className={styles.muted}>
                  {educationError ?? '불러오는 중...'}
                </p>
              ) : edu.items.length === 0 ? (
                <p className={styles.muted}>
                  아직 준비된 교육 자료가 없습니다.
                </p>
              ) : (
                <ul className={styles.education}>
                  {edu.items.map((item) => (
                    <li key={item.recommendation_id}>
                      <p className={styles.topic}>{item.education_topic}</p>
                      <WorkerTranslationNote item={item} />
                      {/* material is filled once AI-generated text is approved by an admin (#111) */}
                      {item.material ? (
                        <p className={styles.material}>{item.material}</p>
                      ) : (
                        <p className={styles.muted}>
                          교육 자료 본문은 준비 중입니다.
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          );
        })}
        <section className={styles.card}>
          <p className={styles.zoneLabel}>다중 구역 통합 교육</p>
          <h2 className={styles.sectionTitle}>통합 교육자료</h2>
          <p className={styles.muted}>
            배정된 구역에 해당하는 자료만 표시됩니다. 통합 자료의 본문은 해당 작업자에게 전체가 공유됩니다.
          </p>
          {bundles === null ? (
            <p className={styles.muted}>{bundleError ?? '통합 자료를 불러오는 중...'}</p>
          ) : bundles.length === 0 ? (
            <p className={styles.muted}>현재 배정된 통합 교육자료가 없습니다.</p>
          ) : (
            <ul className={styles.bundleList}>
              {bundles.map((bundle) => (
                <li key={bundle.bundle_id} className={styles.bundleItem}>
                  <div className={styles.bundleMeta}>
                    <span className={styles.ppeBadge}>{bundle.ppe_type}</span>
                    <span>{bundle.quarter}</span>
                    <span>배정 구역: {bundle.assigned_zones.join(', ')}</span>
                  </div>
                  <h3 className={styles.topic}>{bundle.title}</h3>
                  <WorkerTranslationNote item={bundle} />
                  <p className={styles.material}>{bundle.body}</p>
                  <ul className={styles.checklist}>
                    {bundle.checklist.map((item, index) => (
                      <li key={`${bundle.bundle_id}-${index}`}>{item}</li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          )}
        </section>
      </main>
    </div>
  );
}
