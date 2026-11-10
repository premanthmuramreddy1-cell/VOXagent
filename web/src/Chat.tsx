import { useEffect, useRef, useState } from 'react'
import type { ServerEvent } from './api'
import { api, post, Socket, upload } from './api'
import { Mic, Player } from './audio'

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
  if (item.role === 'user') {
    return (
      <div className="ml-auto max-w-[85%] rounded-md border border-line bg-raised px-3 py-2">
        <p className={`whitespace-pre-wrap break-words ${item.live ? 'text-muted' : ''}`}>{item.text || '…'}</p>
      </div>
    )
  }
  return (
    <div className="max-w-[85%] space-y-2">
      {item.restate && <p className="label normal-case">Request: {item.restate}</p>}
      {item.steps && (
        <ul className="panel divide-y divide-line">
          {item.steps.map((step, index) => (
            <li key={index} className="flex gap-2 px-3 py-1.5 font-mono text-sm">
              <span className={step.done ? 'text-ok' : 'text-muted'} aria-hidden>{step.done ? '[x]' : '[ ]'}</span>
              <span className="sr-only">{step.done ? 'Done:' : 'To do:'}</span>
              {step.text}
            </li>
          ))}
        </ul>
      )}
      {item.tools?.map((tool, index) => (
        <p key={index} className="font-mono text-sm">
          <span className={tool.state === 'failed' ? 'text-danger' : tool.state === 'ok' ? 'text-ok' : 'text-accent'}>
            {tool.state === 'running' ? 'running' : tool.state}
          </span>{' '}
          {tool.skill}
          {tool.error && <span className="text-danger"> — {tool.error}</span>}
          {tool.data?.review_id && (
            <a className="ml-2 underline" href={`#/reviews/${tool.data.review_id}`}>
              Open report ({tool.data.score}/100)
            </a>
          )}
        </p>
      ))}
      {(item.text || item.live) && (
        <p className="whitespace-pre-wrap break-words">
          {item.text}
          {item.live && <span className="text-accent"> ▍</span>}
        </p>
      )}
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
    if (!ok) return setError({ message: 'You are offline. Nothing was sent.', retry: () => send(message, uploads) })
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
          player.current.stop() // barge-in: the agent stops talking the moment you start
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
      setError({ message: 'The microphone is blocked. Allow it in your browser’s site settings, or type your request instead.' })
      return null
    }
  }

  async function talk(start: boolean) {
    if (!start) return mic.current?.stop()
    if (!online) return setError({ message: 'You are offline. Wait for the connection to return.' })
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

  const status = !online ? 'Offline. Reconnecting…' : recording ? 'Listening…' : speaking ? 'Speaking…' : busy ? 'Working…'
    : handsFree ? 'Hands-free: just start talking' : 'Ready'

  return (
    <div className="mx-auto flex h-full w-full max-w-4xl flex-col px-4">
      <div className="no-print flex items-center gap-2 border-b border-line py-2">
        <label className="label" htmlFor="session">Session</label>
        <select id="session" className="field min-w-0 flex-1" value={current} onChange={(e) => setCurrent(e.target.value)}>
          {sessions.map((s) => <option key={s.id} value={s.id}>{s.title}</option>)}
        </select>
        <button className="btn" onClick={async () => {
          const created = await post<Session>('/sessions')
          setSessions((list) => [created, ...list])
          setCurrent(created.id)
        }}>New</button>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto py-4" aria-live="polite">
        {items.length === 0 && (
          <div className="py-12 text-muted">
            <p className="text-ink">Say or type what you need.</p>
            <p>Try “review this file” with a file attached, or paste a GitHub link.</p>
          </div>
        )}
        {items.map((item, index) => <Bubble key={index} item={item} />)}
        {confirm && (
          <div className="panel flex flex-wrap items-center gap-2 border-accent p-3" role="alertdialog" aria-label="Confirm action">
            <p className="flex-1">
              The agent wants to run <b>{confirm.skill}</b>.{' '}
              {confirm.permission === 'critical' ? 'This cannot be undone.' : 'This will change your files.'}
            </p>
            <button className="btn btn-accent" onClick={() => decide(confirm.permission === 'critical' ? 'yes' : 'apply')}>
              {confirm.permission === 'critical' ? 'Yes, do it' : 'Apply'}
            </button>
            <button className="btn" onClick={() => decide('no')}>Cancel</button>
          </div>
        )}
        <div ref={bottom} />
      </div>

      {error && (
        <div className="panel mb-2 flex items-center gap-2 border-danger p-3" role="alert">
          <p className="flex-1 text-danger">{error.message}</p>
          {error.retry && <button className="btn" onClick={error.retry}>Retry</button>}
          <button className="btn" onClick={() => setError(null)}>Dismiss</button>
        </div>
      )}

      <div className="no-print border-t border-line py-3">
        <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1">
          <p className={`label flex-1 ${recording || speaking ? 'text-accent' : ''}`} role="status">{status}</p>
          <label className="label flex min-h-11 items-center gap-2 normal-case">
            <input type="checkbox" checked={handsFree} onChange={toggleHandsFree} /> Hands-free
          </label>
          <label className="label flex min-h-11 items-center gap-2 normal-case">
            <input type="checkbox" checked={speak} onChange={(e) => { setSpeak(e.target.checked); if (!e.target.checked) player.current.stop() }} /> Speak answers
          </label>
        </div>
        {attached && (
          <p className="mb-2 font-mono text-sm">
            Attached: {attached.filename}{' '}
            <button className="underline" onClick={() => setAttached(null)}>remove</button>
          </p>
        )}
        <form className="flex flex-wrap gap-2" onSubmit={submit}>
          <label className="sr-only" htmlFor="message">Message</label>
          <input id="message" className="field min-w-0 flex-1 basis-56" value={text} maxLength={4000}
            placeholder="Type a request" onChange={(e) => setText(e.target.value)} />
          <label className="btn cursor-pointer">
            Attach
            <input type="file" className="sr-only" onChange={(e) => { attach(e.target.files?.[0]); e.target.value = '' }} />
          </label>
          {busy || speaking
            ? <button type="button" className="btn" onClick={stop}>Stop</button>
            : <button type="submit" className="btn" disabled={!text.trim() && !attached}>Send</button>}
          <button type="button" aria-pressed={recording}
            className={`btn btn-accent min-w-28 select-none ${recording ? 'opacity-70' : ''}`}
            onPointerDown={() => talk(true)} onPointerUp={() => talk(false)} onPointerLeave={() => talk(false)}
            onKeyDown={(e) => { if ((e.key === ' ' || e.key === 'Enter') && !e.repeat) { e.preventDefault(); talk(!recording) } }}>
            {recording ? 'Release to send' : 'Hold to talk'}
          </button>
        </form>
      </div>
    </div>
  )
}
