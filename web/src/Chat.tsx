import { useEffect, useRef, useState } from 'react'
import type { ServerEvent } from './api'
import { api, post, Socket, upload } from './api'
import { Mic, Player } from './audio'
import { setSwarmMood } from './swarmEngine'
import { BuildCard, NewsDeck } from './BotDeck'
import { buildCredit, importModel, isModel, playBuild, playNews, releaseBots, type Briefing, type Build } from './botShow'
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
type Extra =
  | { kind: 'diff'; changes: { path: string; kind: string; diff: string }[] }
  | { kind: 'terminal'; text: string }
  | { kind: 'server'; url: string; command: string }
  | { kind: 'email'; to: string[]; subject: string; body: string }
  | { kind: 'form'; url: string; screenshot: string; filled: { field: string }[]; missing: string[] }
  | { kind: 'order'; status: string; note: string; url: string; steps: string[]; total: string; final_button: string; screenshot: string }
  | { kind: 'news'; data: Briefing }
  | { kind: 'build'; build: Build }
type Item = {
  role: 'user' | 'assistant'; text: string; restate?: string; understood?: string; steps?: Step[]; tools?: Tool[]; live?: boolean; extras?: Extra[]
}

const ORDER_STATUS: Record<string, string> = {
  ready: 'Ready, waiting for your yes',
  placed: 'Order placed',
  submitted: 'Final button clicked, no confirmation shown',
  needs_payment: 'Needs your payment details: finish on the store',
  blocked: 'Stopped',
  stuck: 'Could not reach checkout',
}

const diffClass = (line: string) =>
  line.startsWith('+') && !line.startsWith('+++') ? 'diff-add' : line.startsWith('-') && !line.startsWith('---') ? 'diff-del' : 'text-muted'

/** What the agent produced besides speech: staged diffs, terminal output, previews, drafts, filled forms. */
function Extras({ extras }: { extras: Extra[] }) {
  return (
    <>
      {extras.map((extra, i) => {
        if (extra.kind === 'news') return <NewsDeck key={i} data={extra.data} />
        if (extra.kind === 'build') return <BuildCard key={i} build={extra.build} />
        if (extra.kind === 'terminal') {
          return (
            <pre key={i} className="code-layer max-h-64 overflow-auto rounded-xl border border-line bg-surface text-[12px] text-ink">
              {extra.text.slice(-6000)}
            </pre>
          )
        }
        if (extra.kind === 'diff') {
          return (
            <div key={i} className="rounded-xl border border-line bg-surface p-3 font-mono text-xs">
              <p className="mb-2 text-muted">Staged for review: {extra.changes.map((c) => c.path).join(', ')}</p>
              <pre className="code-layer max-h-56 overflow-auto p-0 text-[12px]">
                {extra.changes.flatMap((c) => c.diff.split('\n')).slice(0, 120).map((line, n) => (
                  <div key={n} className={diffClass(line)}>{line || ' '}</div>
                ))}
              </pre>
              <a href="#/workspace" className="mt-2 inline-block text-accent hover:underline">Review each change in the workspace →</a>
            </div>
          )
        }
        if (extra.kind === 'server') {
          return (
            <div key={i} className="overflow-hidden rounded-xl border border-ok/40">
              <a href={extra.url} target="_blank" rel="noreferrer" className="flex items-center gap-2 bg-ok-subtle px-3 py-2 font-mono text-xs text-ok">
                <ExternalLink className="h-3.5 w-3.5" /> Open full page <span className="truncate text-ok/70">{extra.url}</span>
              </a>
              {/* The site opens right here; the preview's own sandbox header keeps it away from VoxAgent's cookies. */}
              <iframe title="Live preview of the site" src={extra.url} className="h-80 w-full bg-white" loading="lazy" />
            </div>
          )
        }
        if (extra.kind === 'email') {
          return (
            <div key={i} className="rounded-xl border border-line bg-surface p-3 text-sm">
              <p className="font-mono text-xs text-muted">To {extra.to.join(', ')}</p>
              <p className="font-semibold">{extra.subject}</p>
              <p className="mt-1 whitespace-pre-wrap text-ink">{extra.body}</p>
            </div>
          )
        }
        if (extra.kind === 'order') {
          const tone = extra.status === 'placed' ? 'text-ok' : extra.status === 'ready' ? 'text-accent' : 'text-muted'
          return (
            <figure key={i} className="overflow-hidden rounded-xl border border-line">
              <img src={`data:image/png;base64,${extra.screenshot}`} alt={`Order page at ${extra.url}`} className="max-h-96 w-full object-cover object-top" />
              <figcaption className="space-y-1 px-3 py-2 font-mono text-xs text-muted">
                <p className={tone}>
                  {ORDER_STATUS[extra.status] ?? extra.status}
                  {extra.total ? ` · total ${extra.total}` : ''}
                  {extra.status === 'ready' && extra.final_button ? ` · next: “${extra.final_button}”` : ''}
                </p>
                {extra.note && <p>{extra.note}</p>}
                {extra.steps.length > 0 && (
                  <details>
                    <summary className="cursor-pointer">{extra.steps.length} step(s) taken</summary>
                    <ol className="mt-1 list-decimal pl-5">{extra.steps.map((s, n) => <li key={n}>{s}</li>)}</ol>
                  </details>
                )}
                {extra.status !== 'ready' && extra.status !== 'placed' && (
                  <a href={extra.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-accent hover:underline">
                    <ExternalLink className="h-3 w-3" /> Open the store
                  </a>
                )}
              </figcaption>
            </figure>
          )
        }
        return (
          <figure key={i} className="overflow-hidden rounded-xl border border-line">
            <img src={`data:image/png;base64,${extra.screenshot}`} alt={`Filled form at ${extra.url}`} className="max-h-96 w-full object-cover object-top" />
            <figcaption className="px-3 py-2 font-mono text-xs text-muted">
              Filled {extra.filled.length} field(s){extra.missing.length ? ` · not filled: ${extra.missing.slice(0, 5).join(', ')}` : ''} · paused before submit
            </figcaption>
          </figure>
        )
      })}
    </>
  )
}
const QUICK = [
  ['Build a website', 'Plans, writes, runs and previews it', 'Build a website for a neighbourhood coffee shop with a menu, opening hours and a contact form'],
  ['Order from a link', 'Walks to checkout, waits for your yes', 'Order 2 iced lattes with oat milk from '],
  ['Review my code', 'Security, bugs and a score', 'Review the codebase for bugs and security vulnerabilities.'],
  ['Edit many files', 'Staged diffs you accept or reject', 'Rename getUser to fetchUser everywhere'],
  ['Build it in 3D', 'The bots assemble any object', 'Make a 3d guitar'],
  ['World briefing', 'Headlines and markets, decoded by bots', "What's going on in the world right now?"],
] as const

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
    case 'agent.understood':
      draft().understood = event.text
      break
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
    case 'changes.proposed':
    case 'server.started':
    case 'email.draft':
    case 'form.preview':
    case 'order.preview':
    case 'terminal.output': {
      const item = draft()
      const extras = [...(item.extras ?? [])]
      const tail = extras[extras.length - 1]
      if (event.type === 'terminal.output') {
        if (tail?.kind === 'terminal') extras[extras.length - 1] = { kind: 'terminal', text: tail.text + event.text }
        else extras.push({ kind: 'terminal', text: event.text })
      } else if (event.type === 'changes.proposed') extras.push({ kind: 'diff', changes: event.changes })
      else if (event.type === 'server.started') extras.push({ kind: 'server', url: event.url, command: event.command })
      else if (event.type === 'email.draft') extras.push({ kind: 'email', to: event.to, subject: event.subject, body: event.body })
      else if (event.type === 'order.preview') extras.push({ kind: 'order', status: event.status, note: event.note, url: event.url, steps: event.steps, total: event.total, final_button: event.final_button, screenshot: event.screenshot })
      else extras.push({ kind: 'form', url: event.url, screenshot: event.screenshot, filled: event.filled, missing: event.missing })
      item.extras = extras
      break
    }
    case 'world.briefing': {
      const item = draft()
      item.extras = [...(item.extras ?? []), { kind: 'news', data: event as unknown as Briefing }]
      break
    }
    case 'swarm.build': {
      const item = draft()
      const build: Build = event.points
        ? { name: event.name, source: 'real model', detail: buildCredit(event.credit) }
        : event.builtin
          ? { name: event.name, source: 'ready-made', detail: 'built from memory' }
          : { name: event.name, source: 'designed', detail: `designed from ${event.parts?.length ?? 0} parts` }
      item.extras = [...(item.extras ?? []), { kind: 'build', build }]
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
        <div className="group relative max-w-xl sm:max-w-2xl rounded-2xl rounded-tr-xs border border-line/60 bg-raised px-4 py-3">
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
          <div className="rounded-xl border border-line bg-surface px-3.5 py-2 font-mono text-xs">
            <span className="font-semibold text-accent uppercase tracking-wider">Plan Target: </span>
            <span className="text-muted">{item.restate}</span>
            {item.understood && (
              <p className="mt-1 text-muted">
                <span className="font-semibold text-accent uppercase tracking-wider">Read as: </span>
                {item.understood}
              </p>
            )}
          </div>
        )}

        {item.extras && item.extras.length > 0 && <Extras extras={item.extras} />}

        {/* Plan Steps */}
        {item.steps && item.steps.length > 0 && (
          <div className="rounded-xl border border-line bg-surface p-3.5 shadow-xs">
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
          <div className="flex flex-col gap-2.5">
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

            {/* Thinking / Reasoning Breakdown */}
            {item.tools?.some((t) => t.data?.thinking?.length > 0) && (
              <div className="rounded-xl border border-line bg-surface p-3.5 font-mono text-xs shadow-xs">
                <div className="flex items-center gap-1.5 font-semibold text-muted">
                  <Sparkles className="h-3.5 w-3.5 text-accent" />
                  <span className="text-ink">Reasoning & Product Evaluation</span>
                </div>
                <ul className="mt-2.5 space-y-1.5 text-ink/80">
                  {item.tools
                    .flatMap((t) => t.data?.thinking || [])
                    .map((step: string, idx: number) => (
                      <li key={idx} className="flex items-start gap-2">
                        <span className="text-accent font-bold">›</span>
                        <span>{step}</span>
                      </li>
                    ))}
                </ul>
                {item.tools?.find((t) => t.data?.comparison)?.data?.comparison && (
                  <div className="mt-2.5 border-t border-line/60 pt-2 text-[11px] text-muted italic">
                    Decision factor: {item.tools.find((t) => t.data?.comparison)?.data?.comparison}
                  </div>
                )}
                {item.tools?.find((t) => t.data?.checkout_url) && (
                  <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-line/60 pt-3">
                    <a
                      className="inline-flex items-center gap-2 rounded-lg bg-accent px-3.5 py-1.5 font-mono text-xs font-bold text-white shadow-xs transition hover:brightness-110"
                      href={item.tools.find((t) => t.data?.checkout_url)?.data.checkout_url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      <span>
                        Open product search ({item.tools.find((t) => t.data?.checkout_url)?.data.currency ?? 'USD'}{' '}
                        {item.tools.find((t) => t.data?.checkout_url)?.data.total_estimated_cost} est.)
                      </span>
                      <ExternalLink className="h-3.5 w-3.5" />
                    </a>
                    {item.tools.find((t) => t.data?.opened_in_browser)?.data.opened_in_browser && (
                      <span className="flex items-center gap-1 text-[11px] text-ok">
                        <CheckCircle2 className="h-3 w-3" />
                        Tab opened on your Mac
                      </span>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Text Answer */}
        {(item.text || item.live) && (
          <div className="relative rounded-2xl rounded-tl-xs border border-line/50 bg-raised px-4 py-3">
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
  const [sidebarOpen, setSidebarOpen] = useState(() => innerWidth >= 768)

  const socket = useRef<Socket | null>(null)
  const player = useRef(new Player())
  const mic = useRef<Mic | null>(null)
  const speakRef = useRef(speak)
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    speakRef.current = speak
  }, [speak])

  /** Switch conversations: the old one's messages, pending yes/no and busy state go with it. */
  function openSession(id: string) {
    if (id === current) return
    setItems([])
    setConfirm(null)
    setBusy(false)
    setCurrent(id)
  }

  useEffect(() => {
    api<Session[]>('/sessions')
      .then(async (list) => {
        const all = list.length ? list : [await post<Session>('/sessions')]
        setSessions(all)
        setCurrent(all[0].id) // first load: nothing to clear yet
      })
      .catch((e) => setError({ message: e.message }))
    player.current.onChange = setSpeaking
    return () => mic.current?.close()
  }, [])

  // The swarm mirrors the conversation: it breathes while listening, swirls while thinking, ripples while speaking.
  useEffect(() => {
    setSwarmMood(recording ? 'listen' : speaking ? 'speak' : busy ? 'think' : 'idle')
  }, [recording, speaking, busy])

  useEffect(() => {
    if (!current) return
    let live = true // a slow history reply for a session the user has left must not land in this one
    api(`/sessions/${current}/messages`)
      .then((rows: any) => live && setItems((list) => [...fromHistory(Array.isArray(rows) ? rows : []), ...list]))
      .catch(() => {})
    const ws = new Socket(current, {
      onStatus: setOnline,
      onAudio: (wav) => speakRef.current && player.current.enqueue(wav),
      onEvent: (event) => {
        if (event.type === 'confirm.request') return setConfirm(event as unknown as Confirm)
        if (event.type === 'error') {
          setError({ message: event.message })
          return setBusy(false)
        }
        if (event.type === 'turn.started') setBusy(true)
        if (event.type === 'tts.stop') player.current.stop()
        if (event.type === 'swarm.build') playBuild(event as any)
        if (event.type === 'world.briefing') playNews(event as unknown as Briefing)
        if (event.type === 'turn.done' || event.type === 'tts.stop') {
          setBusy(false)
          setConfirm(null)
          api<Session[]>('/sessions').then(setSessions).catch(() => {})
        }
        setItems((list) => apply(list, event))
      },
    })
    socket.current = ws
    const voice = player.current
    return () => {
      live = false
      ws.close()
      voice.stop()
    }
  }, [current])

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end' })
  }, [items, confirm])

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

  // The desktop app's global push-to-talk shortcut toggles recording through this event.
  const talkRef = useRef(talk)
  useEffect(() => {
    talkRef.current = talk
  })
  useEffect(() => {
    const toggle = () => talkRef.current(!mic.current?.recording)
    window.addEventListener('vox:talk', toggle)
    return () => window.removeEventListener('vox:talk', toggle)
  }, [])

  async function toggleHandsFree() {
    const on = !handsFree
    const active = on ? await openMic() : mic.current
    active?.setHandsFree(on)
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
    if (isModel(file)) return importFile(file)
    try {
      setAttached(await upload(file))
    } catch (e) {
      setError({ message: (e as Error).message })
    }
  }

  /** A 3D model goes to the bots, not to the server: they rebuild it in the browser. */
  async function importFile(file: File) {
    try {
      const build = await importModel(file)
      setItems((list) => [...list, { role: 'assistant', text: `Imported ${file.name}. The bots rebuilt it.`, extras: [{ kind: 'build', build }] }])
    } catch (e) {
      setError({ message: (e as Error).message })
    }
  }

  // A .glb dropped anywhere on the page goes to the bots; Esc lets them go.
  useEffect(() => {
    const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes('Files')
    const onOver = (e: DragEvent) => hasFiles(e) && e.preventDefault()
    const onDrop = (e: DragEvent) => {
      const file = e.dataTransfer?.files?.[0]
      if (!file) return
      e.preventDefault()
      if (isModel(file)) importFile(file)
      else attach(file)
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && releaseBots()
    addEventListener('dragover', onOver)
    addEventListener('drop', onDrop)
    addEventListener('keydown', onKey)
    return () => {
      removeEventListener('dragover', onOver)
      removeEventListener('drop', onDrop)
      removeEventListener('keydown', onKey)
      releaseBots()
    }
  }, [])

  async function createSession() {
    const created = await post<Session>('/sessions')
    setSessions((list) => [created, ...list])
    openSession(created.id)
  }

  const currentSession = sessions.find((s) => s.id === current)

  return (
    <div className="flex h-full w-full overflow-hidden">
      {/* Left Sidebar for Sessions */}
      <aside
        className={`no-print shrink-0 border-r border-line bg-surface glass flex flex-col transition-all duration-200 ease-in-out ${
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
                onClick={() => openSession(s.id)}
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
      <div className="relative flex-1 flex flex-col h-full min-w-0 overflow-hidden">
        {/* Top Chat Subheader */}
        <div className="no-print h-12 border-b border-line px-4 sm:px-6 flex items-center justify-between shrink-0 bg-surface glass">
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
        {/* The bots' stage: the whole conversation area, with the chat column centred over it. */}
        <div aria-hidden="true" className="pointer-events-none absolute inset-x-0 top-12 bottom-0" data-swarm-stage />
        <div className="chat-rail chat-fade flex-1 overflow-y-auto px-4 sm:px-6 py-6" aria-live="polite">
          <div className="max-w-2xl mx-auto space-y-6">
            {items.length === 0 && (
              <div className="my-auto flex flex-col items-center justify-center pb-10 text-center">
                {/* The swarm's brain floats here, behind the page: this space is left open for it. */}
                <div className="h-44 sm:h-56" aria-hidden="true" />
                <h2 className="font-mono text-base font-semibold text-ink">Say it once. The swarm does the rest.</h2>
                <p className="mt-1.5 max-w-sm text-sm text-muted leading-relaxed">
                  Hold the mic or type. Typos are fine: I fix the spelling and show you what I read.
                </p>
                <div className="mt-8 grid w-full max-w-lg gap-2.5 sm:grid-cols-2">
                  {QUICK.map(([title, hint, prompt]) => (
                    <button
                      key={title}
                      type="button"
                      onClick={() => setText(prompt)}
                      className="glass min-h-11 rounded-xl border border-line bg-raised p-3.5 text-left transition-colors hover:border-accent"
                    >
                      <div className="font-mono text-xs font-semibold text-ink">{title}</div>
                      <div className="mt-0.5 text-xs text-muted">{hint}</div>
                    </button>
                  ))}
                  <label className="glass min-h-11 cursor-pointer rounded-xl border border-dashed border-line bg-raised p-3.5 text-left transition-colors hover:border-accent focus-within:border-accent sm:col-span-2">
                    <div className="font-mono text-xs font-semibold text-ink">Import a 3D model</div>
                    <div className="mt-0.5 text-xs text-muted">Pick or drop a .glb file: the bots rebuild it right here</div>
                    <input type="file" accept=".glb,model/gltf-binary" className="sr-only" onChange={(e) => {
                      const file = e.target.files?.[0]
                      if (file) importFile(file)
                      e.target.value = ''
                    }} />
                  </label>
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
        <div className="chat-rail no-print border-t border-line bg-surface px-4 sm:px-6 py-3.5 glass shrink-0 lg:border-t-0 lg:bg-transparent lg:backdrop-blur-none lg:bg-none">
          <div className="max-w-2xl mx-auto space-y-2.5">
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
                      className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent text-on-accent transition-opacity disabled:opacity-30"
                      title="Send message"
                    >
                      <Send className="h-4 w-4" />
                    </button>
                  )}
                </div>
              </div>

              {/* Primary Voice Button */}
              <button
                type="button"
                aria-pressed={recording}
                aria-label={recording ? 'Listening' : 'Hold to talk'}
                className={`relative flex h-11 min-w-11 px-3 sm:min-w-36 shrink-0 select-none items-center justify-center gap-2 rounded-xl font-mono text-xs font-semibold transition-all duration-150 ${
                  recording
                    ? 'border border-accent bg-accent text-on-accent shadow-lg shadow-accent/25 scale-[1.02]'
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
                    <span className="hidden sm:inline">Listening…</span>
                  </>
                ) : (
                  <>
                    <MicIcon className="h-4 w-4 text-accent" />
                    <span className="hidden sm:inline">Hold to Talk</span>
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
