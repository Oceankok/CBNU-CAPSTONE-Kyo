import { useEffect, useState } from 'react';
import { getSession, logout } from '../api/auth';
import { fetchMyZone } from '../api/zones';
import { fetchMyEducation } from '../api/education';
import { ApiError } from '../api/client';
import type { WorkerEducation, ZoneRule } from '../types';
import { ppeInfo } from '../ppe';
import styles from './WorkerHomePage.module.css';

export default function WorkerHomePage() {
  const session = getSession();
  const [zone, setZone] = useState<ZoneRule | null>(null);
  const [zoneError, setZoneError] = useState<string | null>(null);
  const [education, setEducation] = useState<WorkerEducation | null>(null);
  const [educationError, setEducationError] = useState<string | null>(null);

  useEffect(() => {
    fetchMyZone()
      .then(setZone)
      .catch((e: Error) =>
        setZoneError(
          e instanceof ApiError && e.status === 404
            ? '담당 구역이 아직 지정되지 않았습니다. 관리자에게 문의하세요.'
            : `규칙을 불러오지 못했습니다. (${e.message})`,
        ),
      );
    fetchMyEducation()
      .then(setEducation)
      .catch((e: Error) =>
        setEducationError(`교육 자료를 불러오지 못했습니다. (${e.message})`),
      );
  }, []);

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
          <p className={styles.zoneLabel}>내 작업 구역</p>
          {zone ? (
            <>
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
            </>
          ) : (
            <p className={styles.muted}>{zoneError ?? '불러오는 중...'}</p>
          )}
        </section>
        <section className={styles.card}>
          <h2 className={styles.cardTitle}>
            안전 교육
            {education?.quarter && (
              <span className={styles.quarter}> · {education.quarter}</span>
            )}
          </h2>
          {!education ? (
            <p className={styles.muted}>{educationError ?? '불러오는 중...'}</p>
          ) : education.items.length === 0 ? (
            <p className={styles.muted}>
              {education.empty_reason === 'zone_unassigned'
                ? '담당 구역이 지정되면 구역에 맞는 교육이 표시됩니다.'
                : '아직 준비된 교육 자료가 없습니다.'}
            </p>
          ) : (
            <ul className={styles.education}>
              {education.items.map((item) => (
                <li key={item.recommendation_id}>
                  <p className={styles.topic}>{item.education_topic}</p>
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
      </main>
    </div>
  );
}
