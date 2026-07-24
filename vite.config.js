import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Listen on every network interface (0.0.0.0) so other devices on the
    // same LAN can reach the dev server via this machine's IP.
    host: true,
    // Always use this port; fail loudly instead of silently picking another
    // one, so the URL you share stays predictable.
    port: 5173,
    strictPort: true,
    // Forward /api to the Python backend during development, so the panel can
    // use a relative URL. In Docker, nginx does the same job - which means the
    // frontend code never needs to know the backend's host or port.
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  preview: {
    host: true,
    port: 5173,
    strictPort: true,
  },
})
