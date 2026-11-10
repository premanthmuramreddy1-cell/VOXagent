import { useEffect, useState } from 'react'
import type { User } from './api'
import { api, download, logout, refresh, sign } from './api'
import Chat from './Chat'
import Reviews, { SharedReview } from './Review'

const LANGUAGES = [['', 'Match what I speak'], ['en', 'English'], ['hi', 'Hindi'], ['te', 'Telugu'], ['ta', 'Tamil']]

function useHash() {
  const [hash, setHash] = useState(location.hash)
  useEffect(() => {
    const update = () => setHash(location.hash)
    addEventListener('hashchange', update)
    return () => removeEventListener('hashchange', update)
  }, [])
  return hash.replace(/^#\/?/, '').split('/')
}

function Login({ onUser }: { onUser: (user: User) => void }) {
  const [mode, setMode] = useState<'login' | 'signup'>('login')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    setBusy(true)
    setError('')
    try {
      onUser(await sign(mode, String(form.get('email')), String(form.get('password')), String(form.get('name') ?? '')))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="mx-auto flex min-h-full max-w-sm flex-col justify-center px-4 py-12">
      <h1 className="font-mono text-2xl font-semibold">VoxAgent</h1>
      <p className="mb-6 text-muted">Speak a request. It plans, reviews your code and reports back.</p>
      <form className="space-y-3" onSubmit={submit}>
        {mode === 'signup' && (
          <div>
            <label className="label" htmlFor="name">Name</label>
            <input id="name" name="name" className="field" autoComplete="name" />
          </div>
        )}
        <div>
          <label className="label" htmlFor="email">Email</label>
          <input id="email" name="email" type="email" required className="field" autoComplete="email" />
        </div>
        <div>
          <label className="label" htmlFor="password">Password</label>
          <input id="password" name="password" type="password" required minLength={8} className="field"
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'} aria-describedby="password-hint" />
          <p id="password-hint" className="text-sm text-muted">At least 8 characters.</p>
        </div>
        {error && <p className="text-danger" role="alert">{error}</p>}
        <button className="btn btn-accent w-full" disabled={busy}>{mode === 'login' ? 'Log in' : 'Create account'}</button>
      </form>
      <button className="mt-4 min-h-11 text-left underline" onClick={() => setMode(mode === 'login' ? 'signup' : 'login')}>
        {mode === 'login' ? 'New here? Create an account' : 'Have an account? Log in'}
      </button>
    </main>
  )
}

function Account({ user, onUser }: { user: User; onUser: (user: User | null) => void }) {
  const [usage, setUsage] = useState<any>(null)
  const [note, setNote] = useState('')
  useEffect(() => {
    api('/usage/me').then(setUsage).catch(() => {})
  }, [])

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const body = JSON.stringify({ name: form.get('name'), preferred_language: form.get('language') })
    try {
      onUser(await api('/me', { method: 'PATCH', body }))
      setNote('Saved.')
    } catch (e) {
      setNote((e as Error).message)
    }
  }

  async function remove() {
    if (!confirm('Delete your account and all sessions, reviews and uploads? This cannot be undone.')) return
    await api('/me', { method: 'DELETE' })
    onUser(null)
  }

  return (
    <main className="mx-auto w-full max-w-xl space-y-6 px-4 py-6">
      <h1 className="font-mono text-lg font-semibold">Account</h1>
      <form className="space-y-3" onSubmit={save}>
        <p className="font-mono text-sm text-muted">{user.email} · {user.tier} tier</p>
        <div>
          <label className="label" htmlFor="name">Name</label>
          <input id="name" name="name" className="field" defaultValue={user.name} />
        </div>
        <div>
          <label className="label" htmlFor="language">Reply language</label>
          <select id="language" name="language" className="field" defaultValue={user.preferred_language}>
            {LANGUAGES.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
          </select>
        </div>
        <button className="btn">Save</button>
        {note && <span className="ml-3 text-muted" role="status">{note}</span>}
      </form>
      {usage && (
        <section>
          <h2 className="label mb-2">Last 24 hours</h2>
          <table className="w-full font-mono text-sm">
            <tbody>
              {['message', 'review'].map((kind) => (
                <tr key={kind}>
                  <td className="cell">{kind}s</td>
                  <td className="cell text-right">
                    {usage.last_24h[kind]?.count ?? 0}{usage.daily_limits && ` of ${usage.daily_limits[kind]}`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
      <section className="flex flex-wrap gap-2 border-t border-line pt-4">
        <button className="btn" onClick={async () => download('voxagent-data.json', JSON.stringify(await api('/me/export'), null, 2), 'application/json')}>
          Export my data
        </button>
        <button className="btn" onClick={async () => { await logout(); onUser(null) }}>Log out</button>
        <button className="btn border-danger text-danger" onClick={remove}>Delete account</button>
      </section>
    </main>
  )
}

function Admin() {
  const [data, setData] = useState<any>(null)
  const [metrics, setMetrics] = useState<Record<string, { count: number; p50: number; p95: number }>>({})
  const [error, setError] = useState('')
  useEffect(() => {
    api('/admin/overview').then(setData).catch((e) => setError(e.message))
    api('/metrics').then(setMetrics).catch(() => {})
  }, [])
  if (error) return <p className="p-6 text-danger" role="alert">{error}</p>
  if (!data) return <p className="p-6 text-muted">Loading…</p>
  const table = (title: string, head: string[], rows: (string | number)[][]) => (
    <section>
      <h2 className="label mb-2">{title}</h2>
      <div className="panel overflow-x-auto">
        <table className="w-full font-mono text-sm">
          <thead><tr>{head.map((h) => <th key={h} className="cell text-muted">{h}</th>)}</tr></thead>
          <tbody>
            {rows.length === 0 && <tr><td className="cell text-muted" colSpan={head.length}>Nothing yet.</td></tr>}
            {rows.map((row, i) => <tr key={i}>{row.map((value, j) => <td key={j} className="cell break-words">{value}</td>)}</tr>)}
          </tbody>
        </table>
      </div>
    </section>
  )
  return (
    <main className="mx-auto w-full max-w-4xl space-y-6 px-4 py-6">
      <h1 className="font-mono text-lg font-semibold">Admin</h1>
      {table('Usage, all time', ['kind', 'calls', 'units', 'cost usd'],
        Object.entries<any>(data.usage).map(([kind, u]) => [kind, u.count, u.units, u.cost_usd]))}
      {table('Stage latency, last 24 hours', ['stage', 'calls', 'p50 ms', 'p95 ms'],
        Object.entries(metrics).map(([kind, m]) => [kind, m.count, m.p50, m.p95]))}
      {table('Most-used skills', ['skill', 'runs'], data.skills.map((s: any) => [s.skill, s.runs]))}
      {table(`Users (${data.users.length})`, ['email', 'tier', 'joined'],
        data.users.map((u: any) => [u.email, u.tier, u.created_at.slice(0, 10)]))}
      {table('Recent errors', ['when', 'stage', 'message'],
        data.errors.map((e: any) => [e.ts.slice(0, 19).replace('T', ' '), e.stage, e.message]))}
    </main>
  )
}

export default function App() {
  const [user, setUser] = useState<User | null | undefined>(undefined)
  const [dark, setDark] = useState(() => (localStorage.theme ?? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')) === 'dark')
  const [page, arg] = useHash()

  useEffect(() => {
    refresh().then(setUser)
  }, [])
  useEffect(() => {
    document.documentElement.dataset.theme = localStorage.theme = dark ? 'dark' : 'light'
  }, [dark])

  if (page === 'shared' && arg) return <SharedReview token={arg} />
  if (user === undefined) return <p className="p-6 text-muted">Loading…</p>
  if (!user) return <Login onUser={setUser} />

  const links = [['', 'Talk'], ['reviews', 'Reviews'], ['account', 'Account'], ...(user.is_admin ? [['admin', 'Admin']] : [])]
  return (
    <div className="flex h-full flex-col">
      <header className="no-print flex items-center gap-1 border-b border-line px-4">
        <span className="mr-3 font-mono font-semibold">VoxAgent</span>
        <nav className="flex min-w-0 flex-1 overflow-x-auto" aria-label="Main">
          {links.map(([path, name]) => (
            <a key={path} href={`#/${path}`} aria-current={page === path ? 'page' : undefined}
              className={`flex min-h-11 items-center border-b-2 px-3 font-mono text-sm ${page === path ? 'border-accent text-ink' : 'border-transparent text-muted hover:text-ink'}`}>
              {name}
            </a>
          ))}
        </nav>
        <button className="min-h-11 px-2 font-mono text-sm text-muted hover:text-ink" onClick={() => setDark(!dark)}>
          {dark ? 'Light' : 'Dark'}
        </button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {page === 'reviews' ? <Reviews id={arg} />
          : page === 'account' ? <Account user={user} onUser={(next) => setUser(next)} />
          : page === 'admin' && user.is_admin ? <Admin />
          : <Chat />}
      </div>
    </div>
  )
}
