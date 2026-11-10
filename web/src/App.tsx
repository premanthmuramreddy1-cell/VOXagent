import { useEffect, useState } from 'react'
import type { User } from './api'
import { api, download, logout, refresh, sign } from './api'
import Chat from './Chat'
import Reviews, { SharedReview } from './Review'
import {
  MessageSquare,
  FileCode2,
  User as UserIcon,
  ShieldAlert,
  Sun,
  Moon,
  LogOut,
  Download,
  Trash2,
  Lock,
  Mail,
  ArrowRight,
  Activity,
  CheckCircle2,
  AlertCircle,
  Globe,
  Sliders,
} from 'lucide-react'

const LANGUAGES = [
  ['', 'Match what I speak (Auto)'],
  ['en', 'English'],
  ['hi', 'Hindi (हिन्दी)'],
  ['te', 'Telugu (తెలుగు)'],
  ['ta', 'Tamil (தமிழ்)'],
]

function useHash() {
  const [hash, setHash] = useState(location.hash)
  useEffect(() => {
    const update = () => setHash(location.hash)
    addEventListener('hashchange', update)
    return () => removeEventListener('hashchange', update)
  }, [])
  return hash.replace(/^#\/?/, '').split('/')
}

function Logo({ className = '' }: { className?: string }) {
  return (
    <div className={`flex items-center gap-2.5 select-none ${className}`}>
      <div className="relative flex h-8 w-8 items-center justify-center rounded-xl bg-gradient-to-br from-accent/90 to-accent text-surface shadow-xs">
        <div className="flex items-center gap-0.5">
          <span className="h-2 w-0.5 rounded-full bg-surface"></span>
          <span className="h-4 w-0.5 rounded-full bg-surface"></span>
          <span className="h-5 w-0.5 rounded-full bg-surface"></span>
          <span className="h-3 w-0.5 rounded-full bg-surface"></span>
        </div>
      </div>
      <div className="flex flex-col">
        <span className="font-mono text-xs font-bold tracking-tight text-ink">VOXAGENT</span>
        <span className="font-mono text-[9px] uppercase tracking-widest text-muted">Voice Terminal</span>
      </div>
    </div>
  )
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
    <main className="relative flex min-h-full items-center justify-center px-4 py-12">
      {/* Subtle ambient background glow */}
      <div className="pointer-events-none absolute -top-24 left-1/2 -z-10 h-96 w-96 -translate-x-1/2 rounded-full bg-accent/10 blur-3xl" />

      <div className="w-full max-w-md space-y-6">
        <div className="text-center">
          <div className="inline-block">
            <Logo className="justify-center" />
          </div>
          <p className="mt-3 text-xs text-muted">
            Intelligent voice agent and automated code review instrument.
          </p>
        </div>

        <div className="panel p-6 shadow-md backdrop-blur-sm sm:p-8">
          {/* Mode Switcher */}
          <div className="mb-6 flex rounded-xl border border-line bg-surface p-1">
            <button
              type="button"
              onClick={() => { setMode('login'); setError('') }}
              className={`flex h-9 flex-1 items-center justify-center rounded-lg font-mono text-xs font-semibold transition-all ${
                mode === 'login' ? 'bg-raised text-ink shadow-xs' : 'text-muted hover:text-ink'
              }`}
            >
              Sign In
            </button>
            <button
              type="button"
              onClick={() => { setMode('signup'); setError('') }}
              className={`flex h-9 flex-1 items-center justify-center rounded-lg font-mono text-xs font-semibold transition-all ${
                mode === 'signup' ? 'bg-raised text-ink shadow-xs' : 'text-muted hover:text-ink'
              }`}
            >
              Create Account
            </button>
          </div>

          <form className="space-y-4" onSubmit={submit}>
            {mode === 'signup' && (
              <div>
                <label className="label mb-1.5 block" htmlFor="name">Full Name</label>
                <div className="relative flex items-center">
                  <UserIcon className="pointer-events-none absolute left-3.5 h-4 w-4 text-muted" />
                  <input
                    id="name"
                    name="name"
                    className="field h-11 pl-10"
                    placeholder="Alex Morgan"
                    autoComplete="name"
                  />
                </div>
              </div>
            )}

            <div>
              <label className="label mb-1.5 block" htmlFor="email">Email Address</label>
              <div className="relative flex items-center">
                <Mail className="pointer-events-none absolute left-3.5 h-4 w-4 text-muted" />
                <input
                  id="email"
                  name="email"
                  type="email"
                  required
                  className="field h-11 pl-10"
                  placeholder="name@company.com"
                  autoComplete="email"
                />
              </div>
            </div>

            <div>
              <div className="mb-1.5 flex items-center justify-between">
                <label className="label" htmlFor="password">Password</label>
                <span className="font-mono text-[10px] text-muted">Min 8 chars</span>
              </div>
              <div className="relative flex items-center">
                <Lock className="pointer-events-none absolute left-3.5 h-4 w-4 text-muted" />
                <input
                  id="password"
                  name="password"
                  type="password"
                  required
                  minLength={8}
                  className="field h-11 pl-10"
                  placeholder="••••••••••••"
                  autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                />
              </div>
            </div>

            {error && (
              <div className="flex items-center gap-2 rounded-xl border border-danger/40 bg-danger-subtle p-3 text-xs text-danger" role="alert">
                <AlertCircle className="h-4 w-4 shrink-0" />
                <p>{error}</p>
              </div>
            )}

            <button
              type="submit"
              className="btn btn-accent h-11 w-full text-xs font-bold shadow-sm"
              disabled={busy}
            >
              {busy ? (
                <span className="flex items-center justify-center gap-2 font-mono">
                  <span className="h-3 w-3 animate-spin rounded-full border-2 border-surface border-t-transparent" />
                  Processing…
                </span>
              ) : (
                <span className="flex items-center justify-center gap-2 font-mono">
                  {mode === 'login' ? 'Sign In' : 'Create Free Account'}
                  <ArrowRight className="h-4 w-4" />
                </span>
              )}
            </button>
          </form>

          {/* Quick info badges */}
          <div className="mt-6 border-t border-line pt-5 text-center">
            <div className="grid grid-cols-3 gap-2 font-mono text-[10px] text-muted">
              <div className="rounded-lg border border-line/60 bg-surface/50 p-2.5">
                <span className="font-semibold text-ink">Local AI</span>
                <p className="mt-0.5 text-[9px]">Ollama & Whisper</p>
              </div>
              <div className="rounded-lg border border-line/60 bg-surface/50 p-2.5">
                <span className="font-semibold text-ink">Code Audit</span>
                <p className="mt-0.5 text-[9px]">AST & Linters</p>
              </div>
              <div className="rounded-lg border border-line/60 bg-surface/50 p-2.5">
                <span className="font-semibold text-ink">Private</span>
                <p className="mt-0.5 text-[9px]">Zero Telemetry</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>
  )
}

function Account({ user, onUser }: { user: User; onUser: (user: User | null) => void }) {
  const [usage, setUsage] = useState<any>(null)
  const [note, setNote] = useState('')
  const [isSaving, setIsSaving] = useState(false)

  useEffect(() => {
    api('/usage/me').then(setUsage).catch(() => {})
  }, [])

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setIsSaving(true)
    const form = new FormData(event.currentTarget)
    const body = JSON.stringify({ name: form.get('name'), preferred_language: form.get('language') })
    try {
      onUser(await api('/me', { method: 'PATCH', body }))
      setNote('Profile settings updated.')
      setTimeout(() => setNote(''), 4000)
    } catch (e) {
      setNote((e as Error).message)
    } finally {
      setIsSaving(false)
    }
  }

  async function remove() {
    if (!confirm('Permanently delete your account and all associated sessions, reviews, and uploads?')) return
    await api('/me', { method: 'DELETE' })
    onUser(null)
  }

  return (
    <main className="mx-auto w-full max-w-2xl space-y-6 px-4 py-8">
      {/* Header Profile card */}
      <div className="panel p-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-line bg-surface font-mono text-lg font-bold text-accent">
              {(user.name || user.email)[0].toUpperCase()}
            </div>
            <div>
              <h1 className="text-base font-semibold text-ink">{user.name || 'Anonymous User'}</h1>
              <p className="font-mono text-xs text-muted">{user.email}</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center rounded-full border border-accent/40 bg-accent-subtle px-2.5 py-1 font-mono text-xs font-semibold text-accent uppercase">
              {user.tier} Tier
            </span>
            {user.is_admin && (
              <span className="inline-flex items-center gap-1 rounded-full border border-ok/40 bg-ok-subtle px-2.5 py-1 font-mono text-xs font-semibold text-ok uppercase">
                <CheckCircle2 className="h-3 w-3" /> Admin
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Preferences Form */}
      <div className="panel p-6">
        <h2 className="label mb-4 flex items-center gap-2">
          <Sliders className="h-3.5 w-3.5" /> Preferences & Profile
        </h2>
        <form className="space-y-4" onSubmit={save}>
          <div>
            <label className="label mb-1.5 block" htmlFor="name">Display Name</label>
            <input id="name" name="name" className="field h-11" defaultValue={user.name} placeholder="Your name" />
          </div>
          <div>
            <label className="label mb-1.5 block" htmlFor="language">Voice Reply Language</label>
            <div className="relative flex items-center">
              <Globe className="pointer-events-none absolute left-3.5 h-4 w-4 text-muted" />
              <select id="language" name="language" className="field h-11 pl-10" defaultValue={user.preferred_language}>
                {LANGUAGES.map(([code, name]) => (
                  <option key={code} value={code}>{name}</option>
                ))}
              </select>
            </div>
            <p className="mt-1 text-xs text-muted">When spoken responses are enabled, the agent will reply in this language.</p>
          </div>
          <div className="flex items-center gap-3 pt-2">
            <button className="btn btn-accent h-10 px-5 text-xs font-semibold" disabled={isSaving}>
              {isSaving ? 'Saving…' : 'Save Changes'}
            </button>
            {note && <span className="font-mono text-xs text-accent" role="status">{note}</span>}
          </div>
        </form>
      </div>

      {/* Usage Analytics */}
      {usage && (
        <div className="panel p-6">
          <h2 className="label mb-3 flex items-center gap-2">
            <Activity className="h-3.5 w-3.5" /> Usage (Last 24 Hours)
          </h2>
          <div className="grid grid-cols-2 gap-3">
            {['message', 'review'].map((kind) => {
              const count = usage.last_24h[kind]?.count ?? 0
              const limit = usage.daily_limits?.[kind]
              const percent = limit ? Math.min(100, Math.round((count / limit) * 100)) : 0
              return (
                <div key={kind} className="rounded-xl border border-line bg-surface p-4">
                  <div className="flex items-center justify-between font-mono text-xs">
                    <span className="capitalize text-muted">{kind}s</span>
                    <span className="font-semibold text-ink">
                      {count} {limit ? <span className="text-muted">/ {limit}</span> : ''}
                    </span>
                  </div>
                  {limit && (
                    <div className="mt-2.5 h-1.5 w-full overflow-hidden rounded-full bg-line/60">
                      <div className="h-full bg-accent transition-all duration-300" style={{ width: `${percent}%` }} />
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Danger Zone & Actions */}
      <div className="panel p-6">
        <h2 className="label mb-3 text-muted">Data & Session</h2>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap gap-2">
            <button
              className="btn h-10"
              onClick={async () => download('voxagent-data.json', JSON.stringify(await api('/me/export'), null, 2), 'application/json')}
            >
              <Download className="h-3.5 w-3.5" /> Export Data
            </button>
            <button
              className="btn h-10"
              onClick={async () => { await logout(); onUser(null) }}
            >
              <LogOut className="h-3.5 w-3.5" /> Log Out
            </button>
          </div>
          <button
            className="btn h-10 border-danger/30 text-danger hover:border-danger hover:bg-danger-subtle"
            onClick={remove}
          >
            <Trash2 className="h-3.5 w-3.5" /> Delete Account
          </button>
        </div>
      </div>
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

  if (error) {
    return (
      <div className="p-8">
        <div className="panel border-danger/50 bg-danger-subtle p-4 text-xs text-danger" role="alert">
          {error}
        </div>
      </div>
    )
  }

  if (!data) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <div className="flex items-center gap-2 font-mono text-xs text-muted">
          <span className="h-3 w-3 animate-spin rounded-full border-2 border-accent border-t-transparent" />
          Loading telemetry…
        </div>
      </div>
    )
  }

  const table = (title: string, head: string[], rows: (string | number)[][]) => (
    <section className="space-y-2">
      <h2 className="label">{title}</h2>
      <div className="panel overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full font-mono text-xs">
            <thead>
              <tr className="border-b border-line bg-surface/50">
                {head.map((h) => (
                  <th key={h} className="cell font-semibold text-muted uppercase tracking-wider">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr>
                  <td className="cell text-muted" colSpan={head.length}>No records found.</td>
                </tr>
              ) : (
                rows.map((row, i) => (
                  <tr key={i} className="hover:bg-surface/30">
                    {row.map((value, j) => (
                      <td key={j} className="cell break-words">{value}</td>
                    ))}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )

  return (
    <main className="mx-auto w-full max-w-5xl space-y-8 px-4 py-8">
      <div className="flex items-center justify-between border-b border-line pb-4">
        <div>
          <h1 className="font-mono text-lg font-bold text-ink">System Administration</h1>
          <p className="text-xs text-muted">Telemetry, provider latencies, active user stats, and system diagnostics.</p>
        </div>
        <span className="inline-flex items-center gap-1.5 rounded-full border border-ok/40 bg-ok-subtle px-3 py-1 font-mono text-xs font-semibold text-ok">
          <span className="h-1.5 w-1.5 rounded-full bg-ok animate-pulse" /> Live Telemetry
        </span>
      </div>

      {/* Metrics Cards Grid */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="panel p-4">
          <span className="label block">Registered Users</span>
          <span className="mt-1 font-mono text-2xl font-bold text-ink">{data.users.length}</span>
        </div>
        <div className="panel p-4">
          <span className="label block">Skills Active</span>
          <span className="mt-1 font-mono text-2xl font-bold text-accent">{data.skills.length}</span>
        </div>
        <div className="panel p-4">
          <span className="label block">Recent Errors</span>
          <span className="mt-1 font-mono text-2xl font-bold text-danger">{data.errors.length}</span>
        </div>
        <div className="panel p-4">
          <span className="label block">Telemetry Points</span>
          <span className="mt-1 font-mono text-2xl font-bold text-ink">{Object.keys(metrics).length}</span>
        </div>
      </div>

      {table(
        'Usage & Provider Distribution',
        ['kind', 'calls', 'units', 'cost usd'],
        Object.entries<any>(data.usage).map(([kind, u]) => [kind, u.count, u.units, `$${Number(u.cost_usd).toFixed(4)}`])
      )}

      {table(
        'Latency Benchmarks (Last 24 Hours)',
        ['stage', 'calls', 'p50 ms', 'p95 ms'],
        Object.entries(metrics).map(([kind, m]) => [kind, m.count, `${m.p50} ms`, `${m.p95} ms`])
      )}

      {table(
        'Most-Used Agent Skills',
        ['skill', 'executions'],
        data.skills.map((s: any) => [s.skill, s.runs])
      )}

      {table(
        `User Accounts (${data.users.length})`,
        ['email', 'tier', 'joined'],
        data.users.map((u: any) => [u.email, u.tier, u.created_at.slice(0, 10)])
      )}

      {table(
        'Recent Exception Events',
        ['timestamp', 'stage', 'error message'],
        data.errors.map((e: any) => [e.ts.slice(0, 19).replace('T', ' '), e.stage, e.message])
      )}
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
  if (user === undefined) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="flex items-center gap-2 font-mono text-xs text-muted">
          <span className="h-3 w-3 animate-spin rounded-full border-2 border-accent border-t-transparent" />
          Starting VoxAgent…
        </div>
      </div>
    )
  }
  if (!user) return <Login onUser={setUser} />

  const links = [
    ['', 'Talk', MessageSquare],
    ['reviews', 'Reviews', FileCode2],
    ['account', 'Account', UserIcon],
    ...(user.is_admin ? [['admin', 'Admin', ShieldAlert] as const] : []),
  ] as const

  return (
    <div className="flex h-full flex-col bg-surface text-ink overflow-hidden">
      {/* Top Navigation Bar */}
      <header className="no-print sticky top-0 z-30 flex h-14 shrink-0 items-center justify-between border-b border-line bg-surface/85 px-4 sm:px-6 backdrop-blur-md">
        <div className="flex items-center gap-6">
          <Logo />
          <nav className="flex items-center gap-1" aria-label="Main navigation">
            {links.map(([path, name, Icon]) => {
              const active = page === path
              return (
                <a
                  key={path}
                  href={`#/${path}`}
                  aria-current={active ? 'page' : undefined}
                  className={`flex h-9 items-center gap-1.5 rounded-xl px-3 font-mono text-xs font-semibold transition-all ${
                    active
                      ? 'border border-line bg-raised text-ink shadow-xs'
                      : 'text-muted hover:bg-raised/60 hover:text-ink'
                  }`}
                >
                  <Icon className="h-3.5 w-3.5" />
                  <span>{name}</span>
                </a>
              )
            })}
          </nav>
        </div>

        <div className="flex items-center gap-2">
          {/* User profile tag */}
          <div className="hidden h-9 items-center gap-2 rounded-xl border border-line bg-raised/60 px-3 sm:flex">
            <span className="h-1.5 w-1.5 rounded-full bg-ok" />
            <span className="font-mono text-xs text-muted truncate max-w-40">{user.email}</span>
          </div>

          {/* Theme switcher */}
          <button
            className="flex h-9 w-9 items-center justify-center rounded-xl border border-line bg-raised text-muted transition-colors hover:text-ink"
            onClick={() => setDark(!dark)}
            aria-label="Toggle color theme"
          >
            {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>
        </div>
      </header>

      {/* Main View Area */}
      <div className="min-h-0 flex-1 flex flex-col overflow-hidden">
        {page === 'reviews' ? (
          <div className="flex-1 overflow-y-auto">
            <Reviews id={arg} />
          </div>
        ) : page === 'account' ? (
          <div className="flex-1 overflow-y-auto">
            <Account user={user} onUser={(next) => setUser(next)} />
          </div>
        ) : page === 'admin' && user.is_admin ? (
          <div className="flex-1 overflow-y-auto">
            <Admin />
          </div>
        ) : (
          <Chat />
        )}
      </div>
    </div>
  )
}
