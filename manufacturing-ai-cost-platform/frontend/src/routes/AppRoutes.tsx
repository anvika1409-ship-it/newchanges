/**
 * Routing foundation.
 *
 * Routes are added alongside the endpoints that back them. A route with no
 * implemented endpoint would be a dead end for the user.
 */

import { Navigate, Route, Routes } from 'react-router-dom';
import { DashboardPlaceholder } from '../pages/DashboardPlaceholder';
import { NotFound } from '../pages/NotFound';

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/status" replace />} />
      <Route path="/status" element={<DashboardPlaceholder />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
