// Writes vercel.json: the built app is served by Vercel and every API path is passed on to the backend on the Mac,
// so sign-in cookies stay on one site. Sockets cannot pass through Vercel: build with VITE_WS_URL for those.
// Usage: BACKEND_URL=https://api.example.com node scripts/vercel-config.ts
import { writeFileSync } from 'node:fs'
import { paths } from '../vite.config.ts'

const backend = (process.env.BACKEND_URL ?? '').replace(/\/+$/, '')
if (!/^https:\/\/[^/]+$/.test(backend)) throw new Error('set BACKEND_URL to the https address of the backend')
const config = {
  buildCommand: 'npm run build',
  outputDirectory: 'dist',
  rewrites: [
    ...paths.flatMap((p) => [
      { source: p, destination: `${backend}${p}` },
      { source: `${p}/(.*)`, destination: `${backend}${p}/$1` }, // (.*) also keeps a trailing slash, which :rest* drops
    ]),
    { source: '/(.*)', destination: '/index.html' }, // the app's own routes; Vercel serves real files first
  ],
}
writeFileSync(new URL('../vercel.json', import.meta.url), `${JSON.stringify(config, null, 2)}\n`)
console.log(`vercel.json: ${paths.length} API paths -> ${backend}`)
