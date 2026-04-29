import { BrowserRouter, Routes, Route } from 'react-router-dom';
import AppLayout from './components/AppLayout';
import HomePage from './pages/HomePage';
import ReviewListPage from './pages/ReviewListPage';
import ReviewDetailPage from './pages/ReviewDetailPage';
import StatsPage from './pages/StatsPage';
import RecommendPage from './pages/RecommendPage';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppLayout />}>
          <Route index element={<HomePage />} />
          <Route path="review" element={<ReviewListPage />} />
          <Route path="review/:event_id" element={<ReviewDetailPage />} />
          <Route path="stats" element={<StatsPage />} />
          <Route path="recommend" element={<RecommendPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
