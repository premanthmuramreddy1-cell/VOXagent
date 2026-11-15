import { useEffect, useState } from 'react'
import { CreditCard, GitBranch, Link2, Mail, MessageCircle, Send } from 'lucide-react'
import { api, post } from './api'

type Status = {
  google: { available: boolean; connected: boolean }
  github: { oauth: boolean; connected: boolean }
  telegram: { available: boolean; linked: boolean }
  whatsapp: { available: boolean; linked: boolean }
  deploy: string | null
  billing: { available: boolean; tier: string }
}

function Row({ icon: Icon, name, state, children }: { icon: typeof Mail; name: string; state: string; children?: React.ReactNode }) {
  return (
    <li className="flex flex-wrap items-center gap-3 border-b border-line py-3 last:border-0">
      <Icon className="h-4 w-4 text-muted" />
      <span className="min-w-28 font-mono text-sm">{name}</span>
      <span className="font-mono text-xs text-muted">{state}</span>
      <div className="ml-auto flex flex-wrap gap-2">{children}</div>
    </li>
  )
}

export default function Integrations() {
  const [status, setStatus] = useState<Status | null>(null)
  const [message, setMessage] = useState('')
  const [code, setCode] = useState('')

  const load = () => api<Status>('/integrations').then(setStatus).catch((e) => setMessage(e.message))
  useEffect(() => {
    load()
  }, [])

  const go = async (path: string) => {
    try {
      window.location.href = (await api<{ url: string }>(path)).url
    } catch (e) {
      setMessage((e as Error).message)
    }
  }
  const unlink = (kind: string) => api(`/integrations/${kind}`, { method: 'DELETE' }).then(load)

  if (!status) return null
  return (
    <section className="panel p-5" aria-labelledby="integrations">
      <h2 id="integrations" className="mb-1 font-mono text-sm font-semibold">Connections</h2>
      <p className="mb-3 text-xs text-muted">Tokens are stored encrypted. Sending, posting and deploying always ask you first.</p>
      <ul>
        <Row icon={Mail} name="Google" state={status.google.connected ? 'Gmail + Calendar connected' : status.google.available ? 'not connected' : 'not configured on this server'}>
          {status.google.connected ? <button className="btn" onClick={() => unlink('google')}>Disconnect</button>
            : status.google.available && <button className="btn" onClick={() => go('/integrations/google/connect')}>Connect</button>}
        </Row>
        <Row icon={GitBranch} name="GitHub" state={status.github.connected ? 'connected' : 'not connected'}>
          {status.github.connected ? <button className="btn" onClick={() => unlink('github')}>Disconnect</button> : (
            <>
              {status.github.oauth && <button className="btn" onClick={() => go('/integrations/github/connect')}>Connect</button>}
              <button
                className="btn"
                onClick={async () => {
                  const token = prompt('Paste a GitHub token with the repo scope')
                  if (!token) return
                  try {
                    const { login } = await post<{ login: string }>('/integrations/github/token', { token })
                    setMessage(`Connected as ${login}.`)
                    load()
                  } catch (e) {
                    setMessage((e as Error).message)
                  }
                }}
              >
                Use a token
              </button>
            </>
          )}
        </Row>
        <Row icon={Send} name="Telegram" state={status.telegram.linked ? 'linked' : status.telegram.available ? 'not linked' : 'bot not configured'}>
          {status.telegram.available && !status.telegram.linked && (
            <button className="btn" onClick={() => post<{ code: string }>('/bots/link-code').then((r) => setCode(r.code))}><Link2 className="h-3.5 w-3.5" /> Get code</button>
          )}
        </Row>
        <Row icon={MessageCircle} name="WhatsApp" state={status.whatsapp.linked ? 'linked' : status.whatsapp.available ? 'not linked' : 'not configured'}>
          {status.whatsapp.available && !status.whatsapp.linked && (
            <button className="btn" onClick={() => post<{ code: string }>('/bots/link-code').then((r) => setCode(r.code))}><Link2 className="h-3.5 w-3.5" /> Get code</button>
          )}
        </Row>
        <Row icon={CreditCard} name="Plan" state={status.billing.tier === 'paid' ? 'Paid · no daily limits' : 'Free · daily limits apply'}>
          {status.billing.tier !== 'paid' && status.billing.available && (
            <button className="btn btn-accent" onClick={() => post<{ url: string }>('/billing/checkout').then((r) => { window.location.href = r.url }).catch((e) => setMessage(e.message))}>Upgrade</button>
          )}
        </Row>
      </ul>
      {code && (
        <p className="mt-3 rounded-xl border border-accent/40 bg-accent-subtle px-3 py-2 font-mono text-xs">
          Send <strong className="text-accent">/link {code}</strong> to the Telegram bot, or <strong className="text-accent">link {code}</strong> on WhatsApp, within 10 minutes.
        </p>
      )}
      {message && <p className="mt-3 font-mono text-xs text-muted" role="status">{message}</p>}
    </section>
  )
}
