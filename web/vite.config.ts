import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const api = 'http://localhost:8000'
const paths = ['/auth', '/me', '/sessions', '/uploads', '/reviews', '/shared', '/skills', '/usage', '/admin', '/metrics',
  '/workspaces', '/preview', '/memory', '/notes', '/reminders', '/integrations', '/push', '/billing', '/bots', '/webhooks']

// The dev server proxies the API so the app and backend share one origin.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: { ...Object.fromEntries(paths.map((p) => [p, api])), '/ws': { target: api, ws: true } },
  },
  // `npm run live`: the built app plus the same proxy, reachable through a Cloudflare quick tunnel.
  preview: {
    proxy: { ...Object.fromEntries(paths.map((p) => [p, api])), '/ws': { target: api, ws: true } },
    allowedHosts: ['.trycloudflare.com'],
  },
})
