import { BrowserRouter, Routes, Route, Navigate, Outlet } from 'react-router-dom';
import AppLayout from './components/AppLayout';
import HomePage from './pages/HomePage';
import ReviewListPage from './pages/ReviewListPage';
import ReviewDetailPage from './pages/ReviewDetailPage';
import StatsPage from './pages/StatsPage';
import RecommendPage from './pages/RecommendPage';
import BroadcastPage from './pages/BroadcastPage';
import LoginPage from './pages/LoginPage';
import WorkerHomePage from './pages/WorkerHomePage';
import ZoneRulesPage from './pages/ZoneRulesPage';
import UsersPage from './pages/UsersPage';
import SettingsPage from './pages/SettingsPage';
import { getSession, homePath } from './api/auth';
import type { UserRole } from './types';

// Route guard: no session → /login, wrong role → that role's home.
// UX only — the backend must enforce roles on every API call.
function RequireRole({ role }: { role: UserRole }) {
  const session = getSession();
  if (!session) return <Navigate to="/login" replace />;
  if (session.role !== role) return <Navigate to={homePath(session.role)} replace />;
  return <Outlet />;
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="login" element={<LoginPage />} />

        <Route element={<RequireRole role="worker" />}>
          <Route path="worker" element={<WorkerHomePage />} />
        </Route>

        <Route element={<RequireRole role="admin" />}>
          <Route element={<AppLayout />}>
            <Route index element={<HomePage />} />
            <Route path="review" element={<ReviewListPage />} />
            <Route path="review/:event_id" element={<ReviewDetailPage />} />
            <Route path="stats" element={<StatsPage />} />
            <Route path="recommend" element={<RecommendPage />} />
            <Route path="broadcast" element={<BroadcastPage />} />
            <Route path="zones" element={<ZoneRulesPage />} />
            <Route path="users" element={<UsersPage />} />
            <Route path="settings" element={<SettingsPage />} />
          </Route>
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
