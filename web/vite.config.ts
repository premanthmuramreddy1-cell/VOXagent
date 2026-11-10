import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const api = 'http://localhost:8000'
const paths = ['/auth', '/me', '/sessions', '/uploads', '/reviews', '/shared', '/skills', '/usage', '/admin', '/metrics']

// The dev server proxies the API so the app and backend share one origin.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: { ...Object.fromEntries(paths.map((p) => [p, api])), '/ws': { target: api, ws: true } },
  },
})
