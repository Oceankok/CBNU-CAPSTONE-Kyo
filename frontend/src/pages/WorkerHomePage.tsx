import { useEffect, useState } from 'react';
import { getSession, logout } from '../api/auth';
import { fetchMyZone } from '../api/zones';
import { ApiError } from '../api/client';
import type { ZoneRule } from '../types';
import { ppeInfo } from '../ppe';
import styles from './WorkerHomePage.module.css';

export default function WorkerHomePage() {
  const session = getSession();
  const [zone, setZone] = useState<ZoneRule | null>(null);
  const [zoneError, setZoneError] = useState<string | null>(null);

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
          <h2 className={styles.cardTitle}>안전 교육 자료</h2>
          <p className={styles.muted}>준비 중입니다.</p>
        </section>
      </main>
    </div>
  );
}
