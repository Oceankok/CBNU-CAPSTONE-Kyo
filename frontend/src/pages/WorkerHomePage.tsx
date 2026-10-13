import { useEffect, useState } from 'react';
import { getSession, logout } from '../api/auth';
import { fetchMyZones } from '../api/zones';
import { fetchMyEducation } from '../api/education';
import type { EducationGroup, ZoneRule } from '../types';
import { ppeInfo } from '../ppe';
import styles from './WorkerHomePage.module.css';

export default function WorkerHomePage() {
  const session = getSession();
  const [zones, setZones] = useState<ZoneRule[] | null>(null);
  const [groups, setGroups] = useState<EducationGroup[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [educationError, setEducationError] = useState<string | null>(null);

  useEffect(() => {
    fetchMyZones()
      .then(setZones)
      .catch((e: Error) =>
        setError(`규칙을 불러오지 못했습니다. (${e.message})`),
      );
    fetchMyEducation()
      .then((e) => setGroups(e.groups))
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
      </main>
    </div>
  );
}
