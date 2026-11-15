import { createRoot } from 'react-dom/client';
import { useState } from 'react';
import AutoDecisionSettingsPage from '../../src/pages/AutoDecisionSettingsPage';
import RuleReviewComment from '../../src/components/RuleDecisionExplanation';
import ReviewDetailPage from '../../src/pages/ReviewDetailPage';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
export default function Fixture() {
  const [text, setText] = useState('');
  const [mounted, setMounted] = useState(true);
  const kind = new URLSearchParams(location.search).get('kind');
  if (kind === 'review') return <MemoryRouter initialEntries={['/review/E1']}><Routes><Route path="/review/:event_id" element={<ReviewDetailPage />} /></Routes></MemoryRouter>;
  if (kind === 'audit') return <><label>감사 입력<textarea value={text} onChange={e => setText(e.target.value)} /></label><RuleReviewComment text={text} systemReview /><section aria-label="수동 코멘트 대조"><RuleReviewComment text={text} /></section></>;
  return <><button onClick={() => setMounted(value => !value)}>설정 화면 전환</button>{mounted && <AutoDecisionSettingsPage />}</>;
}
createRoot(document.getElementById('root')!).render(<Fixture />);
