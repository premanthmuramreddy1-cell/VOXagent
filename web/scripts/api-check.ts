// Self-check for api.ts: parallel 401s must share one refresh (refresh tokens work once; a second call logs out).
import assert from 'node:assert/strict'

let refreshCalls = 0
globalThis.fetch = (async (url: string) => {
  if (url === '/auth/refresh') {
    refreshCalls++
    await new Promise((r) => setTimeout(r, 20))
    return new Response(JSON.stringify({ access_token: 't2', user: { id: 'u' } }), { status: 200 })
  }
  return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } })
}) as typeof fetch

const { refresh } = await import('../src/api.ts')
const users = await Promise.all([refresh(), refresh(), refresh()])
assert.equal(refreshCalls, 1, 'three parallel refreshes made more than one request')
assert.ok(users.every((u) => u?.id === 'u'))
await refresh() // once settled, a later refresh is a new request
assert.equal(refreshCalls, 2)
console.log('api ok: parallel refreshes share one request')
