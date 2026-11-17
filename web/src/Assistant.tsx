import { useCallback, useEffect, useState } from 'react'
import { Bell, Brain, Check, ClipboardList, NotebookPen, Pencil, Plus, Search, Trash2, X } from 'lucide-react'
import { api, post } from './api'

type Memory = { id: string; kind: string; text: string }
type Note = { id: string; text: string; tags: string[]; created_at: string }
type Reminder = { id: string; text: string; due: string; repeat: string }

function Section({ icon: Icon, title, hint, children }: { icon: typeof Brain; title: string; hint: string; children: React.ReactNode }) {
  return (
    <section className="panel p-4 sm:p-5">
      <header className="mb-3 flex items-baseline gap-2">
        <Icon className="h-4 w-4 shrink-0 translate-y-0.5 text-accent" />
        <h2 className="font-mono text-sm font-semibold">{title}</h2>
        <p className="ml-auto hidden text-xs text-muted sm:block">{hint}</p>
      </header>
      {children}
    </section>
  )
}

const urlBase64 = (key: string) => {
  const raw = atob((key + '='.repeat((4 - (key.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/'))
  return Uint8Array.from(raw, (c) => c.charCodeAt(0))
}

export default function Assistant() {
  const [memories, setMemories] = useState<Memory[]>([])
  const [notes, setNotes] = useState<Note[]>([])
  const [reminders, setReminders] = useState<Reminder[]>([])
  const [briefing, setBriefing] = useState('')
  const [profile, setProfile] = useState<[string, string][]>([])
  const [query, setQuery] = useState('')
  const [editing, setEditing] = useState<Memory | null>(null)
  const [status, setStatus] = useState('')

  const fail = (e: unknown) => setStatus((e as Error).message)
  const load = useCallback(async () => {
    const [m, n, r, b, p] = await Promise.all([
      api<Memory[]>('/memory'), api<Note[]>('/notes'), api<Reminder[]>('/reminders'),
      api<{ text: string }>('/reminders/briefing'), api<Record<string, string>>('/me/profile'),
    ])
    setMemories(m)
    setNotes(n)
    setReminders(r)
    setBriefing(b.text)
    setProfile(Object.entries(p).length ? Object.entries(p) : [['full_name', ''], ['email', ''], ['phone', ''], ['city', '']])
  }, [])

  useEffect(() => {
    let live = true
    Promise.resolve().then(() => { if (live) return load() }).catch(fail)
    return () => {
      live = false
    }
  }, [load])

  async function searchNotes(q: string) {
    setQuery(q)
    try {
      setNotes(await api<Note[]>(`/notes?q=${encodeURIComponent(q)}`))
    } catch (e) {
      fail(e)
    }
  }

  async function enablePush() {
    try {
      const { key } = await api<{ key: string }>('/push/key')
      if (!key) return setStatus('Push is not configured on this server (VOX_VAPID_PRIVATE_KEY).')
      if ((await Notification.requestPermission()) !== 'granted') return setStatus('Notifications were not allowed.')
      const registration = await navigator.serviceWorker.register('/sw.js')
      await navigator.serviceWorker.ready
      const sub = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64(key) })
      await post('/push/subscribe', sub.toJSON())
      const { sent } = await post<{ sent: number }>('/push/test')
      setStatus(sent ? 'Notifications are on. A test one is on its way.' : 'Subscribed, but the test push did not go through.')
    } catch (e) {
      fail(e)
    }
  }

  return (
    <div className="mx-auto grid w-full max-w-5xl gap-4 px-4 py-6 lg:grid-cols-2">
      <h1 className="sr-only">Notes, reminders and memory</h1>
      {status && <p className="rounded-xl border border-line bg-raised px-3 py-2 font-mono text-xs text-muted lg:col-span-2" role="status">{status}</p>}

      <Section icon={ClipboardList} title="Today" hint="Spoken each morning as a push">
        <p className="text-sm leading-relaxed">{briefing || '…'}</p>
        <button className="btn mt-3" onClick={enablePush}><Bell className="h-3.5 w-3.5" /> Turn on notifications</button>
      </Section>

      <Section icon={Bell} title="Reminders" hint={'Or say "remind me at 9 to call mom"'}>
        <form
          className="mb-3 grid grid-cols-[1fr_auto_auto] gap-2"
          onSubmit={async (e) => {
            e.preventDefault()
            const el = e.currentTarget // React clears currentTarget once the handler awaits
            const form = new FormData(el)
            try {
              await post('/reminders', { text: form.get('text'), when: form.get('when'), repeat: form.get('repeat') })
              el.reset()
              load()
            } catch (err) {
              fail(err)
            }
          }}
        >
          <input name="text" required className="field col-span-3" placeholder="What" aria-label="Reminder text" />
          <input name="when" required type="datetime-local" className="field min-w-0" aria-label="When" />
          <select name="repeat" className="field" aria-label="Repeat">
            <option value="">Once</option><option value="daily">Daily</option>
            <option value="weekdays">Weekdays</option><option value="weekly">Weekly</option>
          </select>
          <button className="btn btn-accent" aria-label="Add reminder"><Plus className="h-3.5 w-3.5" /></button>
        </form>
        <ul className="divide-y divide-line text-sm">
          {reminders.length === 0 && <li className="py-2 text-muted">Nothing scheduled.</li>}
          {reminders.map((r) => (
            <li key={r.id} className="flex items-center gap-3 py-2">
              <time className="w-36 shrink-0 font-mono text-xs text-muted" dateTime={r.due}>
                {new Date(r.due).toLocaleString([], { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}
              </time>
              <span className="min-w-0 flex-1 truncate">{r.text}{r.repeat && <span className="ml-2 label">{r.repeat}</span>}</span>
              <button className="btn-ghost min-h-8 px-2" aria-label={`Mark ${r.text} done`} onClick={() => post(`/reminders/${r.id}/done`).then(load).catch(fail)}><Check className="h-3.5 w-3.5" /></button>
            </li>
          ))}
        </ul>
      </Section>

      <Section icon={NotebookPen} title="Notes" hint={'Say "note that…" to add one'}>
        <div className="mb-3 flex gap-2">
          <label className="relative flex-1">
            <Search className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-muted" />
            <input className="field pl-9" placeholder="Search notes" aria-label="Search notes" value={query} onChange={(e) => searchNotes(e.target.value)} />
          </label>
          <button
            className="btn"
            onClick={async () => {
              const text = prompt('New note')
              if (text) await post('/notes', { text }).then(load).catch(fail)
            }}
          >
            <Plus className="h-3.5 w-3.5" /> Note
          </button>
        </div>
        <ul className="max-h-80 divide-y divide-line overflow-y-auto text-sm">
          {notes.length === 0 && <li className="py-2 text-muted">{query ? 'No matches.' : 'No notes yet.'}</li>}
          {notes.map((n) => (
            <li key={n.id} className="group flex gap-3 py-2">
              <div className="min-w-0 flex-1">
                <p className="whitespace-pre-wrap">{n.text}</p>
                <p className="mt-1 flex flex-wrap gap-1">{n.tags.map((t) => <span key={t} className="rounded-md bg-accent-subtle px-1.5 font-mono text-[11px] text-accent">#{t}</span>)}</p>
              </div>
              <button className="btn-ghost min-h-8 px-2 opacity-60 group-hover:opacity-100" aria-label="Delete note" onClick={() => api(`/notes/${n.id}`, { method: 'DELETE' }).then(load).catch(fail)}><Trash2 className="h-3.5 w-3.5" /></button>
            </li>
          ))}
        </ul>
      </Section>

      <Section icon={Brain} title="What I remember" hint="Used in every conversation">
        <ul className="divide-y divide-line text-sm">
          {memories.length === 0 && <li className="py-2 text-muted">Nothing yet. Say "remember that I prefer Python".</li>}
          {memories.map((m) => (
            <li key={m.id} className="flex items-center gap-2 py-2">
              {editing?.id === m.id ? (
                <form
                  className="flex flex-1 gap-2"
                  onSubmit={(e) => {
                    e.preventDefault()
                    api(`/memory/${m.id}`, { method: 'PUT', body: JSON.stringify({ text: editing.text, kind: m.kind }) })
                      .then(() => { setEditing(null); load() }).catch(fail)
                  }}
                >
                  <input className="field flex-1" aria-label="Memory text" value={editing.text} onChange={(e) => setEditing({ ...editing, text: e.target.value })} />
                  <button className="btn-ghost min-h-8 px-2" aria-label="Save"><Check className="h-3.5 w-3.5" /></button>
                  <button type="button" className="btn-ghost min-h-8 px-2" aria-label="Cancel" onClick={() => setEditing(null)}><X className="h-3.5 w-3.5" /></button>
                </form>
              ) : (
                <>
                  <span className="label w-20 shrink-0">{m.kind}</span>
                  <span className="min-w-0 flex-1">{m.text}</span>
                  <button className="btn-ghost min-h-8 px-2" aria-label={`Edit ${m.text}`} onClick={() => setEditing(m)}><Pencil className="h-3.5 w-3.5" /></button>
                  <button className="btn-ghost min-h-8 px-2" aria-label={`Forget ${m.text}`} onClick={() => api(`/memory/${m.id}`, { method: 'DELETE' }).then(load).catch(fail)}><Trash2 className="h-3.5 w-3.5" /></button>
                </>
              )}
            </li>
          ))}
        </ul>
      </Section>

      <Section icon={ClipboardList} title="Form-filling profile" hint="Encrypted. Never passwords or card numbers.">
        <form
          className="space-y-2"
          onSubmit={async (e) => {
            e.preventDefault()
            try {
              await api('/me/profile', { method: 'PUT', body: JSON.stringify(Object.fromEntries(profile.filter(([k]) => k.trim()))) })
              setStatus('Profile saved (encrypted).')
            } catch (err) {
              fail(err)
            }
          }}
        >
          {profile.map(([key, value], i) => (
            <div key={i} className="grid grid-cols-[minmax(0,2fr)_minmax(0,3fr)] gap-2">
              <input className="field font-mono text-xs" aria-label="Field name" value={key} onChange={(e) => setProfile(profile.map((p, j) => (j === i ? [e.target.value, p[1]] : p)))} />
              <input className="field" aria-label={`Value for ${key}`} value={value} onChange={(e) => setProfile(profile.map((p, j) => (j === i ? [p[0], e.target.value] : p)))} />
            </div>
          ))}
          <div className="flex gap-2">
            <button type="button" className="btn" onClick={() => setProfile([...profile, ['', '']])}><Plus className="h-3.5 w-3.5" /> Field</button>
            <button className="btn btn-accent">Save profile</button>
          </div>
        </form>
      </Section>
    </div>
  )
}
