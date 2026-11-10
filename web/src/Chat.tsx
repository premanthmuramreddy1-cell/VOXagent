import { useEffect, useRef, useState } from 'react'
import type { ServerEvent } from './api'
import { api, post, Socket, upload } from './api'
import { Mic, Player } from './audio'
import {
  Mic as MicIcon,
  Send,
  Paperclip,
  Volume2,
  VolumeX,
  Radio,
  Square,
  Plus,
  CheckCircle2,
  XCircle,
  Loader2,
  Terminal,
  Copy,
  Check,
  AlertCircle,
  Sparkles,
  Bot,
  FileText,
  X,
  ExternalLink,
  PanelLeft,
  MessageSquare,
} from 'lucide-react'

type Step = { text: string; skill: string; done: boolean }
type Tool = { skill: string; state: 'running' | 'ok' | 'failed'; data?: any; error?: string }
type Item = { role: 'user' | 'assistant'; text: string; restate?: string; steps?: Step[]; tools?: Tool[]; live?: boolean }
type Session = { id: string; title: string }
type Confirm = { id: string; skill: string; permission: string }

/** Fold one server event into the message list. */
function apply(items: Item[], event: ServerEvent): Item[] {
  const next = items.slice()
  const last = next[next.length - 1]
  const draft = (): Item => {
    if (last?.role === 'assistant' && last.live) return (next[next.length - 1] = { ...last })
    next.push({ role: 'assistant', text: '', live: true })
    return next[next.length - 1]
  }
  switch (event.type) {
    case 'transcript.partial':
    case 'transcript.final': {
      const live = event.type === 'transcript.partial'
      if (last?.role === 'user' && last.live) next[next.length - 1] = { role: 'user', text: event.text, live }
      else next.push({ role: 'user', text: event.text, live })
      break
    }
    case 'agent.restate':
      draft().restate = event.text
      break
    case 'plan.created':
      draft().steps = event.steps
      break
    case 'plan.step': {
      const item = draft()
      item.steps = item.steps?.map((step, index) => (index === event.index ? { ...step, done: true } : step))
      break
    }
    case 'tool.started': {
      const item = draft()
      item.tools = [...(item.tools ?? []), { skill: event.skill, state: 'running' }]
      break
    }
    case 'tool.finished': {
      const item = draft()
      const state = event.ok ? 'ok' : 'failed'
      item.tools = item.tools?.map((t) => (t.skill === event.skill ? { ...t, state, data: event.data, error: event.error } : t))
      break
    }
    case 'answer.delta':
      draft().text += event.text
      break
    case 'answer.done':
      draft().text = event.text
      break
    case 'clarify':
      draft().text = event.question
      break
    case 'turn.done':
    case 'tts.stop':
      return next.map((item) => (item.live ? { ...item, live: false } : item))
  }
  return next
}

function fromHistory(rows: { role: 'user' | 'assistant'; text: string; plan?: any }[]): Item[] {
  return rows.map((row) => ({
    role: row.role,
    text: row.text,
    restate: row.plan?.restatement,
    steps: row.plan?.steps?.length > 1 ? row.plan.steps : undefined,
  }))
}

function Bubble({ item }: { item: Item }) {
  const [copied, setCopied] = useState(false)

  const copyText = async () => {
    if (!item.text) return
    await navigator.clipboard.writeText(item.text).catch(() => {})
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  if (item.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="group relative max-w-xl sm:max-w-2xl rounded-2xl rounded-tr-xs border border-line bg-raised px-4 py-3 shadow-xs">
          <p className={`whitespace-pre-wrap break-words text-sm leading-relaxed ${item.live ? 'text-muted italic' : 'text-ink'}`}>
            {item.text || '…'}
          </p>
          {item.live && (
            <span className="mt-1.5 flex items-center gap-1.5 font-mono text-[10px] text-accent">
              <span className="h-1.5 w-1.5 rounded-full bg-accent animate-ping" />
              transcribing speech…
            </span>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="flex items-start gap-3">
      {/* Assistant Avatar */}
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-accent/40 bg-accent-subtle text-accent shadow-xs mt-0.5">
        <Bot className="h-4 w-4" />
      </div>

      <div className="group relative min-w-0 max-w-2xl flex-1 space-y-3">
        {/* Restatement Callout */}
        {item.restate && (
          <div className="rounded-xl border border-line bg-surface/70 px-3.5 py-2 font-mono text-xs">
            <span className="font-semibold text-accent uppercase tracking-wider">Plan Target: </span>
            <span className="text-muted">{item.restate}</span>
          </div>
        )}

        {/* Plan Steps */}
        {item.steps && item.steps.length > 0 && (
          <div className="rounded-xl border border-line bg-surface/40 p-3.5 shadow-xs">
            <div className="mb-2.5 flex items-center justify-between font-mono text-xs">
              <span className="label flex items-center gap-1.5">
                <Terminal className="h-3 w-3 text-accent" /> Execution Steps
              </span>
              <span className="text-muted">
                {item.steps.filter((s) => s.done).length} / {item.steps.length} done
              </span>
            </div>
            <ul className="space-y-1.5">
              {item.steps.map((step, index) => (
                <li key={index} className="flex items-start gap-2.5 text-xs">
                  {step.done ? (
                    <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ok" />
                  ) : (
                    <span className="mt-1 h-2 w-2 shrink-0 rounded-full border border-line bg-transparent" />
                  )}
                  <span className={step.done ? 'text-muted line-through' : 'font-mono text-ink'}>
                    {step.text}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Tool Invocations */}
        {item.tools && item.tools.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {item.tools.map((tool, index) => {
              const isRunning = tool.state === 'running'
              const isOk = tool.state === 'ok'
              const isFailed = tool.state === 'failed'

              return (
                <div
                  key={index}
                  className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 font-mono text-xs shadow-xs ${
                    isRunning
                      ? 'border-accent/40 bg-accent-subtle text-accent'
                      : isOk
                      ? 'border-ok/30 bg-ok-subtle text-ok'
                      : 'border-danger/30 bg-danger-subtle text-danger'
                  }`}
                >
                  {isRunning && <Loader2 className="h-3 w-3 animate-spin" />}
                  {isOk && <CheckCircle2 className="h-3 w-3" />}
                  {isFailed && <XCircle className="h-3 w-3" />}
                  <span className="font-semibold">{tool.skill}</span>
                  {tool.error && <span className="text-danger">— {tool.error}</span>}
                  {tool.data?.review_id && (
                    <a
                      className="ml-1 inline-flex items-center gap-1 font-semibold underline hover:opacity-80"
                      href={`#/reviews/${tool.data.review_id}`}
                    >
                      Report ({tool.data.score}/100)
                      <ExternalLink className="h-2.5 w-2.5" />
                    </a>
                  )}
                </div>
              )
            })}
          </div>
        )}

        {/* Text Answer */}
        {(item.text || item.live) && (
          <div className="relative rounded-2xl rounded-tl-xs border border-line/80 bg-raised px-4 py-3 shadow-xs">
            <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-ink">
              {item.text}
              {item.live && (
                <span className="ml-1 inline-block h-3.5 w-1.5 animate-pulse bg-accent align-middle" />
              )}
            </p>

            {item.text && !item.live && (
              <button
                onClick={copyText}
                className="absolute right-2 top-2 rounded-md p-1.5 text-muted opacity-0 transition-opacity hover:bg-surface hover:text-ink group-hover:opacity-100"
                title="Copy response"
                aria-label="Copy response"
              >
                {copied ? <Check className="h-3.5 w-3.5 text-ok" /> : <Copy className="h-3.5 w-3.5" />}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export default function Chat() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [current, setCurrent] = useState('')
  const [items, setItems] = useState<Item[]>([])
  const [text, setText] = useState('')
  const [attached, setAttached] = useState<{ id: string; filename: string } | null>(null)
  const [online, setOnline] = useState(true)
  const [busy, setBusy] = useState(false)
  const [recording, setRecording] = useState(false)
  const [speaking, setSpeaking] = useState(false)
  const [handsFree, setHandsFree] = useState(false)
  const [speak, setSpeak] = useState(true)
  const [confirm, setConfirm] = useState<Confirm | null>(null)
  const [error, setError] = useState<{ message: string; retry?: () => void } | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(true)

  const socket = useRef<Socket | null>(null)
  const player = useRef(new Player())
  const mic = useRef<Mic | null>(null)
  const speakRef = useRef(speak)
  const bottom = useRef<HTMLDivElement>(null)
  speakRef.current = speak

  useEffect(() => {
    api<Session[]>('/sessions')
      .then(async (list) => {
        const all = list.length ? list : [await post<Session>('/sessions')]
        setSessions(all)
        setCurrent(all[0].id)
      })
      .catch((e) => setError({ message: e.message }))
    player.current.onChange = setSpeaking
    return () => mic.current?.close()
  }, [])

  useEffect(() => {
    if (!current) return
    setItems([])
    setConfirm(null)
    setBusy(false)
    api(`/sessions/${current}/messages`).then((rows) => setItems(fromHistory(rows))).catch(() => {})
    const ws = new Socket(current, {
      onStatus: setOnline,
      onAudio: (wav) => speakRef.current && player.current.enqueue(wav),
      onEvent: (event) => {
        if (event.type === 'confirm.request') return setConfirm(event as unknown as Confirm)
        if (event.type === 'error') {
          setError({ message: event.message })
          return setBusy(false)
        }
        if (event.type === 'tts.stop') player.current.stop()
        if (event.type === 'turn.done' || event.type === 'tts.stop') {
          setBusy(false)
          setConfirm(null)
          api<Session[]>('/sessions').then(setSessions).catch(() => {})
        }
        setItems((list) => apply(list, event))
      },
    })
    socket.current = ws
    return () => {
      ws.close()
      player.current.stop()
    }
  }, [current])

  useEffect(() => bottom.current?.scrollIntoView({ block: 'end' }), [items, confirm])

  function send(message: string, uploads: string[]) {
    const ok = socket.current?.send({ type: 'text.input', text: message, upload_ids: uploads, speak })
    if (!ok) return setError({ message: 'Offline. Reconnecting…', retry: () => send(message, uploads) })
    player.current.stop()
    setError(null)
    setBusy(true)
    setItems((list) => [...list, { role: 'user', text: message }])
    setText('')
    setAttached(null)
  }

  function submit(event: React.FormEvent) {
    event.preventDefault()
    const message = text.trim()
    if (!message && !attached) return
    send(message || `Review the attached file ${attached?.filename}`, attached ? [attached.id] : [])
  }

  async function openMic(): Promise<Mic | null> {
    if (!mic.current) {
      mic.current = new Mic({
        onStart: () => {
          player.current.stop()
          socket.current?.send({ type: 'audio.start' })
          setRecording(true)
          setError(null)
        },
        onChunk: (pcm) => socket.current?.send(pcm),
        onStop: () => {
          socket.current?.send({ type: 'audio.stop', speak: speakRef.current })
          setRecording(false)
          setBusy(true)
        },
      })
    }
    try {
      await mic.current.open()
      return mic.current
    } catch {
      setHandsFree(false)
      setError({ message: 'Microphone access blocked. Please enable it in browser permissions or type below.' })
      return null
    }
  }

  async function talk(start: boolean) {
    if (!start) return mic.current?.stop()
    if (!online) return setError({ message: 'You are currently offline. Please wait for reconnection.' })
    ;(await openMic())?.start()
  }

  async function toggleHandsFree() {
    const on = !handsFree
    const active = on ? await openMic() : mic.current
    if (active) active.handsFree = on
    if (!on) active?.stop()
    setHandsFree(on && !!active)
  }

  function stop() {
    player.current.stop()
    socket.current?.send({ type: 'barge_in' })
    setBusy(false)
  }

  function decide(decision: string) {
    if (confirm) socket.current?.send({ type: 'confirm', id: confirm.id, decision })
    setConfirm(null)
  }

  async function attach(file?: File) {
    if (!file) return
    try {
      setAttached(await upload(file))
    } catch (e) {
      setError({ message: (e as Error).message })
    }
  }

  async function createSession() {
    const created = await post<Session>('/sessions')
    setSessions((list) => [created, ...list])
    setCurrent(created.id)
  }

  const currentSession = sessions.find((s) => s.id === current)

  return (
    <div className="flex h-full w-full overflow-hidden">
      {/* Left Sidebar for Sessions */}
      <aside
        className={`no-print shrink-0 border-r border-line bg-surface/40 flex flex-col transition-all duration-200 ease-in-out ${
          sidebarOpen ? 'w-64' : 'w-0'
        } overflow-hidden`}
      >
        {/* Sidebar Header: New Session Button */}
        <div className="p-3 border-b border-line flex items-center gap-2 shrink-0">
          <button
            onClick={createSession}
            className="btn btn-accent h-9 flex-1 text-xs justify-center font-mono font-semibold"
            title="Create a new conversation session"
          >
            <Plus className="h-3.5 w-3.5" />
            <span>New Session</span>
          </button>
        </div>

        {/* Sessions Scrollable List */}
        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          <div className="px-2.5 py-1.5 flex items-center justify-between">
            <span className="label text-[10px]">Chat Sessions</span>
            <span className="font-mono text-[10px] text-muted">{sessions.length}</span>
          </div>

          {sessions.map((s) => {
            const isActive = s.id === current
            return (
              <button
                key={s.id}
                onClick={() => setCurrent(s.id)}
                className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-left font-mono text-xs transition-colors ${
                  isActive
                    ? 'border border-line bg-raised font-semibold text-ink shadow-xs'
                    : 'border border-transparent text-muted hover:bg-raised/60 hover:text-ink'
                }`}
                title={s.title}
              >
                <MessageSquare className={`h-3.5 w-3.5 shrink-0 ${isActive ? 'text-accent' : 'text-muted'}`} />
                <span className="truncate flex-1">{s.title}</span>
              </button>
            )
          })}
        </div>
      </aside>

      {/* Main Chat Conversation Area */}
      <div className="flex-1 flex flex-col h-full min-w-0 overflow-hidden bg-surface">
        {/* Top Chat Subheader */}
        <div className="no-print h-12 border-b border-line px-4 sm:px-6 flex items-center justify-between shrink-0 bg-surface/70 backdrop-blur-xs">
          <div className="flex items-center gap-3 min-w-0">
            {/* Sidebar Toggle Button */}
            <button
              onClick={() => setSidebarOpen(!sidebarOpen)}
              className="flex h-8 w-8 items-center justify-center rounded-lg border border-line bg-raised text-muted transition-colors hover:text-ink"
              title={sidebarOpen ? 'Hide sessions sidebar' : 'Show sessions sidebar'}
            >
              <PanelLeft className="h-4 w-4" />
            </button>

            {/* Current Session Title */}
            <div className="flex items-center gap-2 min-w-0">
              <span className="font-mono text-xs font-semibold text-ink truncate max-w-xs sm:max-w-md">
                {currentSession?.title || 'Active Session'}
              </span>
            </div>
          </div>

          {/* Right Live Status Badge */}
          <div className="flex items-center gap-2 shrink-0">
            <div
              className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-mono text-[11px] font-medium shadow-xs ${
                !online
                  ? 'border-danger/30 bg-danger-subtle text-danger'
                  : recording
                  ? 'border-accent bg-accent-subtle text-accent animate-pulse'
                  : speaking
                  ? 'border-ok/40 bg-ok-subtle text-ok'
                  : busy
                  ? 'border-accent/40 bg-accent-subtle text-accent'
                  : 'border-line bg-raised text-muted'
              }`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  !online
                    ? 'bg-danger'
                    : recording
                    ? 'bg-accent'
                    : speaking
                    ? 'bg-ok animate-pulse'
                    : busy
                    ? 'bg-accent'
                    : 'bg-ok'
                }`}
              />
              <span>
                {!online
                  ? 'Offline'
                  : recording
                  ? 'Listening…'
                  : speaking
                  ? 'Speaking…'
                  : busy
                  ? 'Working…'
                  : handsFree
                  ? 'Hands-Free'
                  : 'Ready'}
              </span>
            </div>
          </div>
        </div>

        {/* Scrollable Messages Stream */}
        <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-6" aria-live="polite">
          <div className="max-w-3xl mx-auto space-y-6">
            {items.length === 0 && (
              <div className="my-auto flex flex-col items-center justify-center py-16 text-center">
                <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-line bg-surface shadow-xs">
                  <Sparkles className="h-7 w-7 text-accent" />
                </div>
                <h2 className="mt-4 font-mono text-base font-semibold text-ink">Welcome to VoxAgent</h2>
                <p className="mt-1.5 max-w-sm text-xs text-muted leading-relaxed">
                  Hold the microphone button to speak, toggle hands-free voice mode, or type your request below.
                </p>

                {/* Quick Prompt Cards */}
                <div className="mt-8 grid w-full max-w-md gap-2.5 sm:grid-cols-2">
                  <button
                    type="button"
                    onClick={() => setText('Review the codebase for bugs and security vulnerabilities.')}
                    className="rounded-xl border border-line bg-raised p-3.5 text-left transition-colors hover:border-accent hover:bg-surface"
                  >
                    <div className="font-mono text-xs font-semibold text-ink">Inspect Codebase</div>
                    <div className="mt-0.5 text-[11px] text-muted">Run AST security & lint audits</div>
                  </button>
                  <button
                    type="button"
                    onClick={() => setText('What skills and capabilities do you have?')}
                    className="rounded-xl border border-line bg-raised p-3.5 text-left transition-colors hover:border-accent hover:bg-surface"
                  >
                    <div className="font-mono text-xs font-semibold text-ink">Explore Skills</div>
                    <div className="mt-0.5 text-[11px] text-muted">List tools and execution stages</div>
                  </button>
                </div>
              </div>
            )}

            {items.map((item, index) => (
              <Bubble key={index} item={item} />
            ))}

            {/* Confirmation Modal / Card */}
            {confirm && (
              <div
                className="panel border-accent bg-raised/95 p-4 shadow-lg backdrop-blur-xs"
                role="alertdialog"
                aria-label="Confirm action"
              >
                <div className="flex items-start gap-3">
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-accent-subtle text-accent mt-0.5">
                    <AlertCircle className="h-5 w-5" />
                  </div>
                  <div className="flex-1">
                    <div className="font-mono text-xs font-bold uppercase tracking-wider text-accent">
                      Permission Required
                    </div>
                    <p className="mt-1 text-sm text-ink leading-relaxed">
                      The agent requests to execute <b className="font-mono text-accent">{confirm.skill}</b>.{' '}
                      {confirm.permission === 'critical'
                        ? 'This action is irreversible.'
                        : 'This will modify workspace files.'}
                    </p>
                    <div className="mt-3.5 flex gap-2">
                      <button
                        className="btn btn-accent h-9 px-4 text-xs font-bold"
                        onClick={() => decide(confirm.permission === 'critical' ? 'yes' : 'apply')}
                      >
                        {confirm.permission === 'critical' ? 'Confirm & Execute' : 'Apply Changes'}
                      </button>
                      <button className="btn h-9 px-4 text-xs" onClick={() => decide('no')}>
                        Decline
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            )}

            <div ref={bottom} />
          </div>
        </div>

        {/* Bottom Error Alert (if present) */}
        {error && (
          <div className="px-4 sm:px-6 pb-2">
            <div className="max-w-3xl mx-auto panel flex items-center gap-3 border-danger/40 bg-danger-subtle p-3 text-xs text-danger shadow-xs" role="alert">
              <AlertCircle className="h-4 w-4 shrink-0" />
              <p className="flex-1">{error.message}</p>
              {error.retry && (
                <button className="btn h-7 border-danger/30 px-2.5 text-xs text-danger" onClick={error.retry}>
                  Retry
                </button>
              )}
              <button className="btn h-7 px-2 text-xs" onClick={() => setError(null)}>
                Dismiss
              </button>
            </div>
          </div>
        )}

        {/* Floating / Docked Input Controls */}
        <div className="no-print border-t border-line bg-surface/90 px-4 sm:px-6 py-3.5 backdrop-blur-md shrink-0">
          <div className="max-w-3xl mx-auto space-y-2.5">
            {/* Attachment Chip */}
            {attached && (
              <div className="inline-flex items-center gap-2 rounded-lg border border-accent/30 bg-accent-subtle px-3 py-1 font-mono text-xs text-accent">
                <FileText className="h-3.5 w-3.5" />
                <span className="font-medium">{attached.filename}</span>
                <button
                  onClick={() => setAttached(null)}
                  className="ml-1 rounded p-0.5 hover:bg-accent/20"
                  title="Remove attachment"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            )}

            {/* Input Bar Form */}
            <form className="flex items-center gap-2.5" onSubmit={submit}>
              <div className="relative flex flex-1 items-center">
                {/* Paperclip on left of input */}
                <label
                  className="absolute left-2 flex h-8 w-8 cursor-pointer items-center justify-center rounded-lg text-muted transition-colors hover:bg-surface hover:text-ink z-10"
                  title="Attach file or code"
                >
                  <Paperclip className="h-4 w-4" />
                  <input
                    type="file"
                    className="sr-only"
                    onChange={(e) => {
                      attach(e.target.files?.[0])
                      e.target.value = ''
                    }}
                  />
                </label>

                {/* Text Field */}
                <input
                  id="message"
                  className="field h-11 pl-11 pr-14 text-sm"
                  value={text}
                  maxLength={4000}
                  placeholder="Ask a question, review files, or hold mic…"
                  onChange={(e) => setText(e.target.value)}
                />

                {/* Send / Stop Action on right of input */}
                <div className="absolute right-2 flex items-center">
                  {busy || speaking ? (
                    <button
                      type="button"
                      className="flex h-7 items-center gap-1.5 rounded-lg border border-danger/30 bg-danger-subtle px-2 font-mono text-xs font-semibold text-danger"
                      onClick={stop}
                      title="Stop generation"
                    >
                      <Square className="h-3 w-3 fill-current" />
                      <span className="text-[11px]">Stop</span>
                    </button>
                  ) : (
                    <button
                      type="submit"
                      disabled={!text.trim() && !attached}
                      className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent text-surface transition-opacity disabled:opacity-30"
                      title="Send message"
                    >
                      <Send className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
              </div>

              {/* Primary Voice Button */}
              <button
                type="button"
                aria-pressed={recording}
                className={`relative flex h-11 min-w-36 shrink-0 select-none items-center justify-center gap-2 rounded-xl font-mono text-xs font-semibold transition-all duration-150 ${
                  recording
                    ? 'border border-accent bg-accent text-surface shadow-lg shadow-accent/25 scale-[1.02]'
                    : 'border border-line bg-raised text-ink hover:border-accent hover:bg-surface active:scale-[0.98]'
                }`}
                onPointerDown={() => talk(true)}
                onPointerUp={() => talk(false)}
                onPointerLeave={() => talk(false)}
                onKeyDown={(e) => {
                  if ((e.key === ' ' || e.key === 'Enter') && !e.repeat) {
                    e.preventDefault()
                    talk(!recording)
                  }
                }}
              >
                {recording ? (
                  <>
                    <div className="flex items-center gap-0.5">
                      <span className="h-2 w-1 rounded-full bg-surface animate-bounce" style={{ animationDelay: '0ms' }} />
                      <span className="h-3.5 w-1 rounded-full bg-surface animate-bounce" style={{ animationDelay: '150ms' }} />
                      <span className="h-2 w-1 rounded-full bg-surface animate-bounce" style={{ animationDelay: '300ms' }} />
                    </div>
                    <span>Listening…</span>
                  </>
                ) : (
                  <>
                    <MicIcon className="h-4 w-4 text-accent" />
                    <span>Hold to Talk</span>
                  </>
                )}
              </button>
            </form>

            {/* Bottom Options & Shortcuts Bar */}
            <div className="flex flex-wrap items-center justify-between gap-2 pt-0.5 text-xs text-muted">
              <div className="flex items-center gap-3">
                {/* Hands-Free Switch */}
                <button
                  type="button"
                  onClick={toggleHandsFree}
                  className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1 font-mono text-xs transition-colors ${
                    handsFree
                      ? 'border border-accent/40 bg-accent-subtle font-semibold text-accent'
                      : 'text-muted hover:text-ink'
                  }`}
                >
                  <Radio className={`h-3.5 w-3.5 ${handsFree ? 'text-accent animate-pulse' : ''}`} />
                  <span>Hands-Free: {handsFree ? 'ON' : 'OFF'}</span>
                </button>

                {/* Voice Reply Switch */}
                <button
                  type="button"
                  onClick={() => {
                    setSpeak(!speak)
                    if (speak) player.current.stop()
                  }}
                  className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1 font-mono text-xs transition-colors ${
                    speak ? 'text-ink font-semibold' : 'text-muted line-through'
                  }`}
                >
                  {speak ? <Volume2 className="h-3.5 w-3.5 text-accent" /> : <VolumeX className="h-3.5 w-3.5 text-muted" />}
                  <span>Voice Replies</span>
                </button>
              </div>

              <div className="hidden font-mono text-[10px] text-muted sm:block">
                Press <kbd className="rounded border border-line bg-surface px-1 py-0.5">Space</kbd> or <kbd className="rounded border border-line bg-surface px-1 py-0.5">Enter</kbd> to talk
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
