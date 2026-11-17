export type User = {
  id: string
  email: string
  name: string
  preferred_language: string
  tier: string
  is_admin: boolean
}
export type ServerEvent = { type: string; seq?: number; [key: string]: any }

let token = ''

let refreshing: Promise<User | null> | null = null

/** Trade the refresh cookie for a new access token. Null means the user must log in.
 *  Refresh tokens work once, so parallel 401s share one request instead of racing and logging the user out. */
export function refresh(): Promise<User | null> {
  refreshing ??= (async () => {
    try {
      const response = await fetch('/auth/refresh', { method: 'POST' })
      if (response.status !== 200) return null // 204: no session cookie, the visitor is logged out
      const body = await response.json()
      token = body.access_token
      return body.user as User
    } catch {
      return null
    } finally {
      refreshing = null
    }
  })()
  return refreshing
}

export async function api<T = any>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  const headers = new Headers(init.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (typeof init.body === 'string') headers.set('Content-Type', 'application/json')
  let response: Response
  try {
    response = await fetch(path, { ...init, headers })
  } catch {
    throw new Error('Cannot reach the server. Check your connection and try again.')
  }
  if (response.status === 401 && retry && !path.startsWith('/auth/') && (await refresh())) {
    return api(path, init, false)
  }
  const isJson = response.headers.get('content-type')?.includes('json')
  const body = isJson ? await response.json() : await response.text()
  if (!response.ok) {
    const detail = isJson ? body.detail : body
    throw new Error(typeof detail === 'string' ? detail : 'Please check what you entered and try again.')
  }
  return body
}

/** Authenticated fetch that returns the Response itself: for zip downloads and streamed output. */
export async function raw(path: string, init: RequestInit = {}, retry = true): Promise<Response> {
  const headers = new Headers(init.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (typeof init.body === 'string') headers.set('Content-Type', 'application/json')
  const response = await fetch(path, { ...init, headers })
  if (response.status === 401 && retry && (await refresh())) return raw(path, init, false)
  if (!response.ok) {
    const text = await response.text()
    let detail = text
    try {
      detail = JSON.parse(text).detail ?? text
    } catch {
      /* plain-text error */
    }
    throw new Error(typeof detail === 'string' && detail ? detail : `Request failed (${response.status})`)
  }
  return response
}

export const post = <T = any>(path: string, body?: unknown) =>
  api<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

/** Change the password; other devices are signed out and this one gets fresh tokens. */
export async function changePassword(current: string, next: string): Promise<User> {
  const body = await post('/auth/password', { current, new: next })
  token = body.access_token
  return body.user
}

export async function sign(mode: 'login' | 'signup', email: string, password: string, name: string): Promise<User> {
  const body = await post(`/auth/${mode}`, { email, password, name })
  token = body.access_token
  return body.user
}

export async function googleSign(credential: string): Promise<User> {
  const body = await post('/auth/google', { credential })
  token = body.access_token
  return body.user
}

export async function logout() {
  await post('/auth/logout').catch(() => {})
  token = ''
}

export async function upload(file: File): Promise<{ id: string; filename: string }> {
  const form = new FormData()
  form.append('file', file)
  return api('/uploads', { method: 'POST', body: form })
}

export function download(name: string, content: string, type: string) {
  const link = document.createElement('a')
  link.href = URL.createObjectURL(new Blob([content], { type }))
  link.download = name
  link.click()
  URL.revokeObjectURL(link.href)
}

type Handlers = {
  onEvent: (event: ServerEvent) => void
  onAudio: (wav: ArrayBuffer) => void
  onStatus: (online: boolean) => void
}

/** The last event number to count as seen once the server says where it is. First connect: the server's position.
 *  A server behind us restarted and counts from zero again: keeping our old number would drop every new event. */
export const resumeFrom = (last: number, server: number) => (last < 0 || server < last ? server : last)

/** WebSocket that reconnects with backoff and asks the server to replay what it missed. */
export class Socket {
  private ws?: WebSocket
  private lastSeq = -1
  private delay = 500
  private closed = false
  private queue: string[] = [] // text sent before the socket was ready; flushed once it is
  private sessionId: string
  private handlers: Handlers

  constructor(sessionId: string, handlers: Handlers) {
    this.sessionId = sessionId
    this.handlers = handlers
    void this.open()
  }

  private async open() {
    if (this.closed) return
    if (!token) await refresh() // a socket opened without a token is closed with 4401 and drops the first message
    if (this.closed) return
    // VITE_WS_URL: the backend's own address when the app is hosted elsewhere (Vercel cannot carry sockets)
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
    const base = import.meta.env.VITE_WS_URL || `${scheme}://${location.host}`
    const ws = new WebSocket(`${base}/ws/sessions/${this.sessionId}`)
    ws.binaryType = 'arraybuffer'
    ws.onopen = () => ws.send(JSON.stringify({ type: 'auth', token }))
    ws.onmessage = (message) => {
      if (typeof message.data !== 'string') return this.handlers.onAudio(message.data)
      const event: ServerEvent = JSON.parse(message.data)
      if (event.type === 'ready') {
        this.delay = 500
        this.handlers.onStatus(true)
        // First connect: history comes from REST, so start at the server's position.
        const seq = event.seq ?? 0
        if (this.lastSeq >= 0 && seq > this.lastSeq) this.send({ type: 'resume', last_seq: this.lastSeq })
        this.lastSeq = resumeFrom(this.lastSeq, seq)
        for (const queued of this.queue.splice(0)) ws.send(queued)
        return
      }
      if (event.seq) {
        if (event.seq <= this.lastSeq) return
        this.lastSeq = event.seq
      }
      this.handlers.onEvent(event)
    }
    ws.onclose = (close) => {
      if (this.closed) return
      this.handlers.onStatus(false)
      // Run async work in a detached promise so onclose itself returns void (not a Promise).
      void (async () => {
        // the access token expired; no new one means the session is gone (signed out, account deleted): stop
        // retrying and show the sign-in screen instead of reconnecting forever
        if (close.code === 4401 && !(await refresh())) return location.reload()
        setTimeout(() => void this.open(), this.delay)
        this.delay = Math.min(this.delay * 2, 10_000)
      })()
    }
    this.ws = ws
  }

  /** Text is queued while (re)connecting and sent once the server is ready. Returns false only for audio sent
   *  while offline (live speech is useless later) or when the queue is full, so the caller can say nothing was sent. */
  send(message: object | ArrayBuffer): boolean {
    const ready = this.ws?.readyState === WebSocket.OPEN
    if (message instanceof ArrayBuffer) {
      if (ready) this.ws!.send(message)
      return ready
    }
    if (ready) this.ws!.send(JSON.stringify(message))
    else if (this.queue.length < 20) this.queue.push(JSON.stringify(message))
    else return false
    return true
  }

  close() {
    this.closed = true
    this.ws?.close()
  }
}
