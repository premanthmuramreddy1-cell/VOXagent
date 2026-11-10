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

/** Trade the refresh cookie for a new access token. Null means the user must log in. */
export async function refresh(): Promise<User | null> {
  try {
    const response = await fetch('/auth/refresh', { method: 'POST' })
    if (!response.ok) return null
    const body = await response.json()
    token = body.access_token
    return body.user
  } catch {
    return null
  }
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

export const post = <T = any>(path: string, body?: unknown) =>
  api<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

export async function sign(mode: 'login' | 'signup', email: string, password: string, name: string): Promise<User> {
  const body = await post(`/auth/${mode}`, { email, password, name })
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

/** WebSocket that reconnects with backoff and asks the server to replay what it missed. */
export class Socket {
  private ws?: WebSocket
  private lastSeq = -1
  private delay = 500
  private closed = false
  private sessionId: string
  private handlers: Handlers

  constructor(sessionId: string, handlers: Handlers) {
    this.sessionId = sessionId
    this.handlers = handlers
    this.open()
  }

  private open() {
    if (this.closed) return
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${scheme}://${location.host}/ws/sessions/${this.sessionId}`)
    ws.binaryType = 'arraybuffer'
    ws.onopen = () => ws.send(JSON.stringify({ type: 'auth', token }))
    ws.onmessage = (message) => {
      if (typeof message.data !== 'string') return this.handlers.onAudio(message.data)
      const event: ServerEvent = JSON.parse(message.data)
      if (event.type === 'ready') {
        this.delay = 500
        this.handlers.onStatus(true)
        // First connect: history comes from REST, so start at the server's position.
        if (this.lastSeq < 0) this.lastSeq = event.seq ?? 0
        else if ((event.seq ?? 0) > this.lastSeq) this.send({ type: 'resume', last_seq: this.lastSeq })
        return
      }
      if (event.seq) {
        if (event.seq <= this.lastSeq) return
        this.lastSeq = event.seq
      }
      this.handlers.onEvent(event)
    }
    ws.onclose = async (close) => {
      if (this.closed) return
      this.handlers.onStatus(false)
      if (close.code === 4401) await refresh() // the access token expired
      setTimeout(() => this.open(), this.delay)
      this.delay = Math.min(this.delay * 2, 10_000)
    }
    this.ws = ws
  }

  /** Returns false when offline, so the caller can tell the user nothing was sent. */
  send(message: object | ArrayBuffer): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) return false
    this.ws.send(message instanceof ArrayBuffer ? message : JSON.stringify(message))
    return true
  }

  close() {
    this.closed = true
    this.ws?.close()
  }
}
