import { createRoot } from 'react-dom/client';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import EventMediaPanel from '../../src/components/EventMediaPanel';
import ReviewDetailPage from '../../src/pages/ReviewDetailPage';

const eventId = 'EVT_eb9bab67a08c4500a2dbca819625a163';
const review = new URLSearchParams(location.search).has('review');
createRoot(document.getElementById('root')!).render(review
  ? <MemoryRouter initialEntries={['/review/'+eventId]}><Routes><Route path="/review/:event_id" element={<ReviewDetailPage />} /></Routes></MemoryRouter>
  : <EventMediaPanel eventId={eventId} cameraId="FV_B" finalReview={false} onChange={async () => {}} />);
