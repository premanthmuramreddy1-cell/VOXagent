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

const { refresh, resumeFrom } = await import('../src/api.ts')
assert.equal(resumeFrom(-1, 7), 7, 'first connect starts at the server position')
assert.equal(resumeFrom(5, 9), 5, 'a reconnect keeps its place so missed events replay')
assert.equal(resumeFrom(57, 0), 0, 'a restarted server (counter behind ours) must not have its events dropped')
const users = await Promise.all([refresh(), refresh(), refresh()])
assert.equal(refreshCalls, 1, 'three parallel refreshes made more than one request')
assert.ok(users.every((u) => u?.id === 'u'))
await refresh() // once settled, a later refresh is a new request
assert.equal(refreshCalls, 2)
console.log('api ok: parallel refreshes share one request')
