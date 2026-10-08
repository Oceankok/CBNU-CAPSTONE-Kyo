import { lazy, Suspense } from 'react';
import {
  BrowserRouter,
  Routes,
  Route,
  Navigate,
  Outlet,
} from 'react-router-dom';
import LoginPage from './pages/LoginPage';
import { getSession, homePath } from './api/auth';
import type { UserRole } from './types';

// Route-level code splitting: worker devices never download admin pages (or Recharts)
const AppLayout = lazy(() => import('./components/AppLayout'));
const HomePage = lazy(() => import('./pages/HomePage'));
const ReviewListPage = lazy(() => import('./pages/ReviewListPage'));
const ReviewDetailPage = lazy(() => import('./pages/ReviewDetailPage'));
const StatsPage = lazy(() => import('./pages/StatsPage'));
const RecommendPage = lazy(() => import('./pages/RecommendPage'));
const BroadcastPage = lazy(() => import('./pages/BroadcastPage'));
const ZoneRulesPage = lazy(() => import('./pages/ZoneRulesPage'));
const UsersPage = lazy(() => import('./pages/UsersPage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));
const FieldNodesPage = lazy(() => import('./pages/FieldNodesPage'));
const EquipmentPage = lazy(() => import('./pages/EquipmentPage'));
const WorkerHomePage = lazy(() => import('./pages/WorkerHomePage'));

// Route guard: no session → /login, wrong role → that role's home.
// UX only — the backend must enforce roles on every API call.
function RequireRole({ role }: { role: UserRole }) {
  const session = getSession();
  if (!session) return <Navigate to="/login" replace />;
  if (session.role !== role)
    return <Navigate to={homePath(session.role)} replace />;
  return <Outlet />;
}

export default function App() {
  return (
    <BrowserRouter>
      <Suspense fallback={null}>
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
              <Route path="nodes" element={<FieldNodesPage />} />
              <Route path="equipment" element={<EquipmentPage />} />
            </Route>
          </Route>

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  );
}
