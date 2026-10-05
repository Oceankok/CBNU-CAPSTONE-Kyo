import { useState, useEffect } from 'react';
import { useOutletContext } from 'react-router-dom';
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
import { fetchStats, generateStats, calcTrend } from '../api/stats';
import type { QuarterlyStats } from '../types';
import type { LayoutContext } from '../components/AppLayout';
import styles from './StatsPage.module.css';

const PPE_LABEL: Record<string, string> = {
  helmet: '안전모',
  vest: '안전조끼',
};

export default function StatsPage() {
  // Quarter comes from the TopBar selector (shared with Home / Recommend)
  const { quarter } = useOutletContext<LayoutContext>();
  const [stats, setStats] = useState<QuarterlyStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshMsg, setRefreshMsg] = useState<string | null>(null);

  // Load stats whenever the selected quarter changes
  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchStats(quarter)
      .then(setStats)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [quarter]);

  // Re-aggregate backend stats then reload the chart data
  function handleRefresh() {
    setRefreshing(true);
    setRefreshMsg(null);
    generateStats(quarter)
      .then(() => fetchStats(quarter))
      .then((data) => {
        setStats(data);
        setRefreshMsg('통계가 갱신되었습니다.');
        setTimeout(() => setRefreshMsg(null), 3000);
      })
      .catch((e) => setError(e.message))
      .finally(() => setRefreshing(false));
  }

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
        <button
          className={styles.refreshBtn}
          onClick={handleRefresh}
          disabled={refreshing || loading}
          title="통계 새로고침"
        >
          {refreshing ? '갱신 중…' : '↻ 통계 새로고침'}
        </button>
      </div>

      {error && <p style={{ color: '#e53e3e', marginBottom: '1rem' }}>⚠ {error}</p>}
      {refreshMsg && <p style={{ color: '#276749', marginBottom: '1rem' }}>✓ {refreshMsg}</p>}
      {loading && <p style={{ color: '#718096', marginBottom: '1rem' }}>데이터를 불러오는 중...</p>}

      {stats && (
        <>
      <div className={styles.cardRow}>
        <SummaryCard label="확정 위반" value={stats.summary.confirmed_count} trend={calcTrend(stats.trend)} accent />
        <SummaryCard label="보류" value={stats.summary.hold_count} />
        <SummaryCard label="오탐" value={stats.summary.false_positive_count} />
        <SummaryCard label="전체 후보" value={stats.summary.candidate_count} />
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

