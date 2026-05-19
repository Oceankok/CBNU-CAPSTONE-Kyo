import { useState, useEffect } from 'react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  LineChart,
  Line,
  Legend,
  ResponsiveContainer,
} from 'recharts';
import SummaryCard from '../components/SummaryCard';
import { AVAILABLE_QUARTERS } from '../mock';
import { fetchStats } from '../api/stats';
import type { QuarterlyStats } from '../types';
import styles from './StatsPage.module.css';

const PPE_LABEL: Record<string, string> = {
  helmet: '안전모',
  vest: '안전조끼',
};

export default function StatsPage() {
  const [quarter, setQuarter] = useState(AVAILABLE_QUARTERS[0]);
  const [stats, setStats] = useState<QuarterlyStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchStats(quarter)
      .then(setStats)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [quarter]);

  const ppeData = (stats?.by_ppe_type ?? []).map((p) => ({
    name: PPE_LABEL[p.ppe_type] ?? p.ppe_type,
    확정위반: p.confirmed_count,
  }));

  const zoneData = (stats?.by_zone ?? []).map((z) => ({
    name: z.zone_name,
    확정위반: z.confirmed_count,
  }));

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <h2 className={styles.title}>분기별 통계</h2>
        <select
          className={styles.quarterSelect}
          value={quarter}
          onChange={(e) => setQuarter(e.target.value)}
        >
          {AVAILABLE_QUARTERS.map((q) => (
            <option key={q} value={q}>
              {q}
            </option>
          ))}
        </select>
      </div>

      {error && <p style={{ color: '#e53e3e', marginBottom: '1rem' }}>⚠ {error}</p>}
      {loading && <p style={{ color: '#718096', marginBottom: '1rem' }}>데이터를 불러오는 중...</p>}

      {stats && (
        <>
      <div className={styles.cardRow}>
        <SummaryCard label="후보 이벤트" value={stats.summary.candidate_count} sub="AI 추출 총합" />
        <SummaryCard
          label="확정 위반"
          value={stats.summary.confirmed_count}
          sub="검토 완료"
          trend={12}
        />
        <SummaryCard
          label="오탐"
          value={stats.summary.false_positive_count}
          sub="AI 오탐지"
          trend={-5}
        />
        <SummaryCard label="보류" value={stats.summary.hold_count} sub="추가 검토 필요" />
      </div>

      <div className={styles.chartRow}>
        <section className={styles.section}>
          <h3>PPE 유형별 확정 위반</h3>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={ppeData} margin={{ top: 8, right: 20, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="name" tick={{ fontSize: 13 }} />
              <YAxis tick={{ fontSize: 12 }} />
              <Tooltip />
              <Bar dataKey="확정위반" fill="#3182ce" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </section>

        <section className={styles.section}>
          <h3>구역별 확정 위반</h3>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={zoneData} margin={{ top: 8, right: 20, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="name" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 12 }} />
              <Tooltip />
              <Bar dataKey="확정위반" fill="#e67e22" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </section>
      </div>

      <section className={styles.section}>
        <h3>분기별 위반 추이</h3>
        <ResponsiveContainer width="100%" height={240}>
          <LineChart data={stats.trend} margin={{ top: 8, right: 24, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
            <XAxis dataKey="quarter" tick={{ fontSize: 12 }} />
            <YAxis tick={{ fontSize: 12 }} />
            <Tooltip />
            <Legend />
            <Line
              type="monotone"
              dataKey="helmet"
              name="안전모"
              stroke="#3182ce"
              strokeWidth={2}
              dot={{ r: 4 }}
            />
            <Line
              type="monotone"
              dataKey="vest"
              name="안전조끼"
              stroke="#e67e22"
              strokeWidth={2}
              dot={{ r: 4 }}
            />
          </LineChart>
        </ResponsiveContainer>
      </section>
        </>
      )}
    </div>
  );
}

