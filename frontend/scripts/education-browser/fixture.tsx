import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import EducationMaterialEditor, { type EducationMaterialDraft } from '../../src/components/EducationMaterialEditor';
import EducationBundleEditor from '../../src/components/EducationBundleEditor';
import RecommendPage from '../../src/pages/RecommendPage';
import { MemoryRouter, Routes, Route, Outlet } from 'react-router-dom';

function QuarterLayout() {
  const [quarter, setQuarter] = useState('2026-Q4');
  return <>
    <button onClick={() => setQuarter('2026-Q3')}>이전 분기로 전환</button>
    <Outlet key={quarter} context={{ quarter }} />
  </>;
}

export default function Fixture() {
  const [draft, setDraft] = useState<EducationMaterialDraft | null>(null);
  const [mounted, setMounted] = useState(true);
  const [scopeConflicts, setScopeConflicts] = useState(0);
  const kind = new URLSearchParams(location.search).get('kind');
  if (kind === 'recommend') return <MemoryRouter><Routes><Route element={<QuarterLayout />}>
    <Route path="/" element={<RecommendPage />} />
  </Route></Routes></MemoryRouter>;
  return kind === 'bundle' ? <EducationBundleEditor quarter="2026-Q4" recommendationIds={[]} selectionRevision={0} /> : <>
    <button onClick={() => setMounted(!mounted)}>외부 편집기 전환</button>
    <output data-testid="draft">{JSON.stringify(draft)}</output>
    <output data-testid="scope-conflicts">{scopeConflicts}</output>
    {mounted && <EducationMaterialEditor recommendationId="r1" scope={{
      recommendation_id: 'r1', quarter: '2026-Q4', zone_name: 'A', ppe_type: 'helmet', education_topic: 'Safety',
    }} draft={draft ?? undefined} onDraftChange={setDraft} onScopeConflict={() => setScopeConflicts(n => n + 1)} />}
  </>;
}

createRoot(document.getElementById('root')!).render(<Fixture />);
