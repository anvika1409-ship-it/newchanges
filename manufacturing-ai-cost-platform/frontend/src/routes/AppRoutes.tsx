/**
 * Routing foundation.
 *
 * Routes are added alongside the endpoints that back them. A route with no
 * implemented endpoint would be a dead end for the user
 * (AI_DEVELOPMENT_RULES.md section 19).
 */

import { Navigate, Route, Routes } from 'react-router-dom';
import { Dashboard } from '../pages/Dashboard';
import { OptimizationCenter } from '../pages/OptimizationCenter';
import { QualityInspection } from '../pages/QualityInspection';
import { StatusPage } from '../pages/StatusPage';
import { WhatIfSimulator } from '../pages/WhatIfSimulator';
import { NotFound } from '../pages/NotFound';

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route path="/dashboard" element={<Dashboard />} />
      <Route path="/quality" element={<QualityInspection />} />
      <Route path="/optimization" element={<OptimizationCenter />} />
      <Route path="/simulator" element={<WhatIfSimulator />} />
      <Route path="/status" element={<StatusPage />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
