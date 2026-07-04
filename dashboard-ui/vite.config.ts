import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Served by Flask at /dash in prod; dev proxies data/asset routes to the
// local Flask app (run `./.venv/bin/python dashboard.py` alongside `npm run dev`).
export default defineConfig({
  plugins: [react()],
  base: '/dash/',
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8080',
      '/sprite': 'http://127.0.0.1:8080',
      '/psprite': 'http://127.0.0.1:8080',
      '/audio': 'http://127.0.0.1:8080',
    },
  },
})
