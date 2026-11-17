import { useCallback, useEffect, useState } from 'react'
import { AlarmClockPlus, Bell, Brain, Check, ClipboardList, Flame, Receipt, ListChecks, Minus, NotebookPen, Pencil, Plus, Search, Trash2, X } from 'lucide-react'
import { api, post } from './api'

type Memory = { id: string; kind: string; text: string }
type Note = { id: string; text: string; tags: string[]; created_at: string }
type Payment = { id: string; name: string; kind: string; amount: number; currency: string; cycle: string; next_due: string; autopay: boolean; due_in: number }
type Habit = { id: string; name: string; target: number; unit: string; times: string[]; done: number; streak: number }
type Reminder = { id: string; text: string; due: string; repeat: string }

const anchor = (title: string) => title.toLowerCase().replace(/[^a-z]+/g, '-').replace(/^-|-$/g, '')

function Section({ icon: Icon, title, hint, children }: { icon: typeof Brain; title: string; hint: string; children: React.ReactNode }) {
  return (
    <section id={anchor(title)} className="panel scroll-mt-48 p-4 sm:p-5">
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
  const [changing, setChanging] = useState<Reminder | null>(null) // the reminder the form above is editing
  const [briefing, setBriefing] = useState('')
  const [profile, setProfile] = useState<[string, string][]>([])
  const [query, setQuery] = useState('')
  const [editing, setEditing] = useState<Memory | null>(null)
  const [draft, setDraft] = useState<{ id: string | null; text: string } | null>(null) // id null: a new note
  const [status, setStatus] = useState('')
  const [lists, setLists] = useState<Record<string, { id: string; text: string }[]>>({})
  const [habits, setHabits] = useState<Habit[]>([])
  const [bills, setBills] = useState<{ items: Payment[]; monthly: Record<string, number> }>({ items: [], monthly: {} })

  const fail = (e: unknown) => setStatus((e as Error).message)
  const load = useCallback(async () => {
    const [m, n, r, b, p, l, h, pay] = await Promise.all([
      api<Memory[]>('/memory'), api<Note[]>('/notes'), api<Reminder[]>('/reminders'),
      api<{ text: string }>('/reminders/briefing'), api<Record<string, string>>('/me/profile'), api('/lists'), api<Habit[]>('/habits'), api('/payments'),
    ])
    setLists(l)
    setHabits(h)
    setBills(pay)
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

  async function saveDraft() {
    if (!draft?.text.trim()) return setDraft(null)
    const body = JSON.stringify({ text: draft.text })
    try {
      await (draft.id ? api(`/notes/${draft.id}`, { method: 'PUT', body }) : post('/notes', { text: draft.text }))
      setDraft(null)
      await (query ? searchNotes(query) : load())
    } catch (e) {
      fail(e)
    }
  }

  const composer = (
    <form className="flex flex-col gap-2 py-2" onSubmit={(e) => { e.preventDefault(); saveDraft() }}>
      <textarea
        autoFocus
        rows={4}
        className="field min-h-24 resize-y py-2"
        aria-label="Note text"
        placeholder="Write a note. #tags are picked up."
        value={draft?.text ?? ''}
        onChange={(e) => setDraft((d) => d && { ...d, text: e.target.value })}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); saveDraft() }
          if (e.key === 'Escape') setDraft(null)
        }}
      />
      <div className="flex items-center justify-end gap-2">
        <span className="mr-auto font-mono text-[11px] text-muted">⌘/Ctrl + Enter saves · Esc cancels</span>
        <button type="button" className="btn-ghost min-h-8 px-3" onClick={() => setDraft(null)}>Cancel</button>
        <button className="btn btn-accent min-h-8 px-3" disabled={!draft?.text.trim()}>Save</button>
      </div>
    </form>
  )

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
    <div className="mx-auto grid w-full max-w-5xl grid-cols-1 gap-4 px-4 py-6 lg:grid-cols-2">
      <h1 className="sr-only">Notes, reminders and memory</h1>
      <nav aria-label="Jump to a section" className="sticky top-14 z-20 -mx-4 flex gap-1.5 overflow-x-auto border-b border-line bg-surface px-4 py-2 lg:col-span-2">
        {([
          ['Today', 'Today', null], ['Bills', 'Bills & subscriptions', bills.items.length], ['Habits', 'Habits', habits.length],
          ['Lists', 'Lists', Object.values(lists).reduce((n, items) => n + items.length, 0)],
          ['Reminders', 'Reminders', reminders.length], ['Notes', 'Notes', notes.length],
          ['Memory', 'What I remember', memories.length], ['Profile', 'Form-filling profile', null],
        ] as [string, string, number | null][]).map(([label, title, count]) => (
          <a
            key={title}
            href={`#${anchor(title)}`}
            onClick={(e) => {
              e.preventDefault() // the hash routes pages here, so scroll instead of changing it
              document.getElementById(anchor(title))?.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
            }}
            className="inline-flex min-h-8 shrink-0 items-center gap-1.5 rounded-full border border-line px-3 font-mono text-xs text-muted hover:border-accent hover:text-ink [@media(pointer:coarse)]:min-h-11"
          >
            {label}
            {count ? <span className="text-accent">{count}</span> : null}
          </a>
        ))}
      </nav>
      {status && <p className="rounded-xl border border-line bg-raised px-3 py-2 font-mono text-xs text-muted lg:col-span-2" role="status">{status}</p>}

      <Section icon={ClipboardList} title="Today" hint="Spoken each morning as a push">
        <p className="text-sm leading-relaxed">{briefing || '…'}</p>
        <button className="btn mt-3" onClick={enablePush}><Bell className="h-3.5 w-3.5" /> Turn on notifications</button>
      </Section>

      <Section icon={Receipt} title="Bills & subscriptions" hint={'Tracked only, never paid. Or say "I pay 649 a month for Netflix"'}>
        <form
          className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-[minmax(0,1fr)_6rem_auto_auto_auto_auto]"
          onSubmit={async (e) => {
            e.preventDefault()
            const el = e.currentTarget
            const form = new FormData(el)
            try {
              await post('/payments', {
                name: form.get('name'), amount: Number(form.get('amount') || 0), kind: form.get('kind'),
                cycle: form.get('cycle'), next_due: form.get('due'),
              })
              el.reset()
              load()
            } catch (err) {
              fail(err)
            }
          }}
        >
          <input name="name" required className="field col-span-2 min-w-0 sm:col-span-1" placeholder="Rent" aria-label="Bill name" />
          <input name="amount" type="number" min={0} step="any" className="field min-w-0" placeholder="Amount" aria-label="Amount" />
          <input name="due" type="date" required className="field min-w-0" aria-label="Next due" />
          <select name="kind" className="field" aria-label="Kind"><option value="bill">Bill</option><option value="subscription">Subscription</option></select>
          <select name="cycle" className="field" aria-label="Every" defaultValue="month">
            <option value="week">Weekly</option><option value="month">Monthly</option><option value="year">Yearly</option>
          </select>
          <button className="btn btn-accent col-span-2 sm:col-span-1" aria-label="Track bill"><Plus className="h-3.5 w-3.5" /></button>
        </form>
        <ul className="divide-y divide-line text-sm">
          {bills.items.length === 0 && <li className="py-2 text-muted">Nothing tracked.</li>}
          {bills.items.map((b) => {
            const soon = b.due_in <= 3
            return (
              <li key={b.id} className="flex items-center gap-2 py-2">
                <div className="min-w-0 flex-1">
                  <p className="truncate">{b.name}{b.autopay && <span className="ml-2 label">autopay</span>}</p>
                  <p className={`font-mono text-xs ${b.due_in < 0 ? 'font-semibold text-danger' : soon ? 'text-accent' : 'text-muted'}`}>
                    {b.amount ? `${b.currency}${b.amount.toLocaleString()} / ${b.cycle} · ` : ''}
                    {b.due_in < 0 ? `Overdue since ${b.next_due}` : b.due_in === 0 ? 'Due today' : `Due in ${b.due_in} ${b.due_in === 1 ? 'day' : 'days'} (${b.next_due})`}
                  </p>
                </div>
                <button className="btn-ghost min-h-8 px-2" title="Mark paid" aria-label={`Mark ${b.name} paid`} onClick={() => post(`/payments/${b.id}/paid`).then(load).catch(fail)}><Check className="h-3.5 w-3.5" /></button>
                <button className="btn-ghost min-h-8 px-2" title="Stop tracking" aria-label={`Stop tracking ${b.name}`} onClick={() => confirm(`Stop tracking ${b.name}? This does not cancel the service.`) && api(`/payments/${b.id}`, { method: 'DELETE' }).then(load).catch(fail)}><Trash2 className="h-3.5 w-3.5" /></button>
              </li>
            )
          })}
        </ul>
        {Object.keys(bills.monthly).length > 0 && (
          <p className="mt-2 font-mono text-xs text-muted">
            About {Object.entries(bills.monthly).map(([c, v]) => `${c}${v.toLocaleString()}`).join(' + ')} a month in total
          </p>
        )}
      </Section>

      <Section icon={Flame} title="Habits" hint={'Or say "I drank 2 glasses of water"'}>
        <form
          className="mb-3 grid grid-cols-3 gap-2 sm:grid-cols-[minmax(0,1fr)_4.5rem_minmax(0,6rem)_minmax(0,8rem)_auto]"
          onSubmit={async (e) => {
            e.preventDefault()
            const el = e.currentTarget
            const form = new FormData(el)
            try {
              const times = String(form.get('times') ?? '').split(/[\s,]+/).filter(Boolean)
              await post('/habits', { name: form.get('name'), target: Number(form.get('target')), unit: form.get('unit') || 'time', times })
              el.reset()
              load()
            } catch (err) {
              fail(err)
            }
          }}
        >
          <input name="name" required className="field col-span-3 min-w-0 sm:col-span-1" placeholder="Water" aria-label="Habit name" />
          <input name="target" type="number" min={1} max={100} defaultValue={1} className="field min-w-0" aria-label="Times a day" />
          <input name="unit" className="field min-w-0" placeholder="glass" aria-label="Unit" />
          <input name="times" className="field min-w-0" placeholder="09:00, 21:00" aria-label="Nudge times (optional)" />
          <button className="btn btn-accent col-span-3 sm:col-span-1" aria-label="Track habit"><Plus className="h-3.5 w-3.5" /></button>
        </form>
        <ul className="divide-y divide-line text-sm">
          {habits.length === 0 && <li className="py-2 text-muted">No habits yet.</li>}
          {habits.map((h) => {
            const met = h.done >= h.target
            const log = (count: number) => post(`/habits/${h.id}/log`, { count }).then(load).catch(fail)
            return (
              <li key={h.id} className="flex items-center gap-2 py-2">
                <div className="min-w-0 flex-1">
                  <p className="truncate">{h.name}{h.times.length > 0 && <span className="ml-2 font-mono text-xs text-muted">at {h.times.join(', ')}</span>}{h.streak > 1 && <span className="ml-2 label">{h.streak}-day streak</span>}</p>
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-line/60" role="progressbar" aria-label={`${h.name} today`} aria-valuenow={h.done} aria-valuemin={0} aria-valuemax={h.target}>
                    <div className={`h-full ${met ? 'bg-ok' : 'bg-accent'}`} style={{ width: `${Math.min(100, (h.done / h.target) * 100)}%` }} />
                  </div>
                </div>
                <span className={`w-14 shrink-0 text-right font-mono text-xs ${met ? 'text-ok' : 'text-muted'}`}>{h.done}/{h.target}</span>
                <button className="btn-ghost min-h-8 px-2" aria-label={`Undo one ${h.name}`} title="Undo one" disabled={!h.done} onClick={() => log(-1)}><Minus className="h-3.5 w-3.5" /></button>
                <button className="btn-ghost min-h-8 px-2" aria-label={`Log one ${h.name}`} title={`Log one ${h.unit}`} onClick={() => log(1)}><Plus className="h-3.5 w-3.5" /></button>
                <button className="btn-ghost min-h-8 px-2" aria-label={`Stop tracking ${h.name}`} title="Stop tracking" onClick={() => confirm(`Stop tracking ${h.name}? Its history goes too.`) && api(`/habits/${h.id}`, { method: 'DELETE' }).then(load).catch(fail)}><Trash2 className="h-3.5 w-3.5" /></button>
              </li>
            )
          })}
        </ul>
      </Section>

      <Section icon={ListChecks} title="Lists" hint={'Or say "add milk and eggs to my grocery list"'}>
        <form
          className="mb-3 grid grid-cols-[minmax(0,8rem)_minmax(0,1fr)_auto] gap-2"
          onSubmit={async (e) => {
            e.preventDefault()
            const el = e.currentTarget
            const form = new FormData(el)
            try {
              await post('/lists/items', { list: form.get('list'), text: form.get('item') })
              ;(el.elements.namedItem('item') as HTMLInputElement).value = '' // keep the list name for the next item
              load()
            } catch (err) {
              fail(err)
            }
          }}
        >
          <input name="list" className="field min-w-0" placeholder="groceries" aria-label="List name" list="list-names" />
          <datalist id="list-names">{Object.keys(lists).map((n) => <option key={n} value={n} />)}</datalist>
          <input name="item" required className="field min-w-0" placeholder="Item" aria-label="Item to add" />
          <button className="btn btn-accent" aria-label="Add to list"><Plus className="h-3.5 w-3.5" /></button>
        </form>
        {Object.keys(lists).length === 0 && <p className="py-2 text-sm text-muted">No lists yet.</p>}
        {Object.entries(lists).map(([name, items]) => (
          <div key={name} className="mb-3">
            <h3 className="label mb-1 capitalize">{name} · {items.length}</h3>
            <ul className="flex flex-wrap gap-1.5 text-sm">
              {items.map((i) => (
                <li key={i.id}>
                  <button
                    className="inline-flex min-h-8 items-center gap-1.5 rounded-full border border-line px-3 hover:border-ok hover:text-ok [@media(pointer:coarse)]:min-h-11"
                    aria-label={`Tick off ${i.text} from ${name}`}
                    title="Tick off"
                    onClick={() => api(`/lists/items/${i.id}`, { method: 'DELETE' }).then(load).catch(fail)}
                  >
                    <Check className="h-3 w-3" /> {i.text}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </Section>

      <Section icon={Bell} title="Reminders" hint={'Or say "remind me at 9 to call mom"'}>
        <form
          key={changing?.id ?? 'new'}
          className="mb-3 grid grid-cols-[minmax(0,1fr)_auto_auto] gap-2"
          onSubmit={async (e) => {
            e.preventDefault()
            const el = e.currentTarget // React clears currentTarget once the handler awaits
            const form = new FormData(el)
            try {
              const body = { text: form.get('text'), when: form.get('when'), repeat: form.get('repeat') }
              if (changing) await api(`/reminders/${changing.id}`, { method: 'PUT', body: JSON.stringify(body) })
              else await post('/reminders', body)
              el.reset()
              setChanging(null)
              load()
            } catch (err) {
              fail(err)
            }
          }}
        >
          <input name="text" required className="field col-span-3" placeholder="What" aria-label="Reminder text" defaultValue={changing?.text} />
          <input name="when" required type="datetime-local" className="field min-w-0" aria-label="When" defaultValue={changing?.due.slice(0, 16)} />
          <select name="repeat" className="field" aria-label="Repeat" defaultValue={changing?.repeat ?? ''}>
            <option value="">Once</option><option value="daily">Daily</option>
            <option value="weekdays">Weekdays</option><option value="weekly">Weekly</option>
          </select>
          {changing ? (
            <div className="col-span-3 flex gap-2">
              <button className="btn btn-accent">Save reminder</button>
              <button type="button" className="btn" onClick={() => setChanging(null)}>Cancel</button>
            </div>
          ) : (
            <button className="btn btn-accent" aria-label="Add reminder"><Plus className="h-3.5 w-3.5" /></button>
          )}
        </form>
        <ul className="divide-y divide-line text-sm">
          {reminders.length === 0 && <li className="py-2 text-muted">Nothing scheduled.</li>}
          {reminders.map((r) => {
            const late = new Date(r.due).getTime() < Date.now()
            return (
            <li key={r.id} className="flex items-center gap-3 py-2">
              <div className="flex min-w-0 flex-1 flex-col sm:flex-row sm:items-center sm:gap-3">
              <time className={`shrink-0 font-mono text-xs sm:w-36 ${late ? 'font-semibold text-danger' : 'text-muted'}`} dateTime={r.due}>
                {late && <span className="mr-1">Overdue ·</span>}
                {new Date(r.due).toLocaleString([], { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}
              </time>
              <span className="min-w-0 flex-1 truncate">{r.text}{r.repeat && <span className="ml-2 label">{r.repeat}</span>}</span>
              </div>
              {!r.repeat && (
                <button className="btn-ghost min-h-8 px-2" title="Snooze 1 hour" aria-label={`Snooze ${r.text} for 1 hour`} onClick={() => post(`/reminders/${r.id}/snooze`, { minutes: 60 }).then(load).catch(fail)}><AlarmClockPlus className="h-3.5 w-3.5" /></button>
              )}
              <button className="btn-ghost min-h-8 px-2" title="Edit" aria-label={`Edit ${r.text}`} onClick={() => setChanging(r)}><Pencil className="h-3.5 w-3.5" /></button>
              <button className="btn-ghost min-h-8 px-2" title={r.repeat ? 'Done for this time' : 'Done'} aria-label={`Mark ${r.text} done`} onClick={() => post(`/reminders/${r.id}/done`).then(load).catch(fail)}><Check className="h-3.5 w-3.5" /></button>
              <button
                className="btn-ghost min-h-8 px-2"
                title={r.repeat ? 'Stop this repeating reminder' : 'Remove'}
                aria-label={`Remove ${r.text}`}
                onClick={() => confirm(r.repeat ? `Stop "${r.text}" for good?` : `Remove "${r.text}"?`) && api(`/reminders/${r.id}`, { method: 'DELETE' }).then(load).catch(fail)}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </li>
            )
          })}
        </ul>
      </Section>

      <Section icon={NotebookPen} title="Notes" hint={'Say "note that…" to add one'}>
        <div className="mb-3 flex gap-2">
          <label className="relative flex-1">
            <Search className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-muted" />
            <input className="field pl-9" placeholder="Search notes" aria-label="Search notes" value={query} onChange={(e) => searchNotes(e.target.value)} />
          </label>
          <button className="btn" onClick={() => setDraft({ id: null, text: '' })}>
            <Plus className="h-3.5 w-3.5" /> Note
          </button>
        </div>
        {draft?.id === null && composer}
        <ul className="max-h-80 divide-y divide-line overflow-y-auto text-sm">
          {notes.length === 0 && <li className="py-2 text-muted">{query ? 'No matches.' : 'No notes yet.'}</li>}
          {notes.map((n) => (
            draft?.id === n.id ? <li key={n.id}>{composer}</li> : <li key={n.id} className="group flex gap-3 py-2">
              <div className="min-w-0 flex-1">
                <p className="whitespace-pre-wrap">{n.text}</p>
                <p className="mt-1 flex flex-wrap gap-1">
                  {n.tags.map((t) => (
                    <button key={t} type="button" title={`Show notes about ${t}`} onClick={() => searchNotes(t)} className="rounded-md bg-accent-subtle px-1.5 font-mono text-[11px] text-accent hover:underline [@media(pointer:coarse)]:min-h-8">
                      #{t}
                    </button>
                  ))}
                </p>
              </div>
              <button className="btn-ghost min-h-8 px-2 opacity-60 group-hover:opacity-100" aria-label="Edit note" onClick={() => setDraft({ id: n.id, text: n.text })}><Pencil className="h-3.5 w-3.5" /></button>
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
