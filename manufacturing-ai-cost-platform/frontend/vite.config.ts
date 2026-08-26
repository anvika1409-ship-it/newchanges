import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

/**
 * Dev server proxies /api to the backend so the browser sees a single origin
 * and no CORS credentials are needed during local development. In
 * production this same job is done by nginx (see nginx.conf) — the app
 * itself never talks to the backend on a different origin or holds a
 * backend credential (ARCHITECTURE.md section 7, SECURITY.md section 6).
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_DEV_API_TARGET ?? 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
});
