import { useEffect, useState } from 'react'
import { api, download, post, upload } from './api'

type Issue = {
  severity: string
  category: string
  file: string
  line: number
  explanation: string
  why: string
  fix: string
  source: string
}
type Report = {
  id: string
  source: string
  score: number
  breakdown: Record<string, number>
  issues: Issue[]
  not_run: string[]
  created_at: string
}

const TONE: Record<string, string> = { critical: 'text-danger', high: 'text-danger', medium: 'text-warn', low: 'text-muted' }

export function ReportView({ report, shared }: { report: Report; shared?: boolean }) {
  const [link, setLink] = useState('')
  const [error, setError] = useState('')

  async function share() {
    try {
      const { token } = await post(`/reviews/${report.id}/share`)
      const url = `${location.origin}/#/shared/${token}`
      setLink(url)
      await navigator.clipboard?.writeText(url).catch(() => {})
    } catch (e) {
      setError((e as Error).message)
    }
  }

  async function exportMarkdown() {
    try {
      download(`review-${report.id.slice(0, 8)}.md`, await api(`/reviews/${report.id}/export`), 'text/markdown')
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <article className="space-y-6">
      <header className="flex flex-wrap items-end gap-x-6 gap-y-2 border-b border-line pb-4">
        <div>
          <p className="label">Quality score</p>
          <p className="font-mono text-5xl font-semibold leading-none">
            {report.score}<span className="text-xl text-muted">/100</span>
          </p>
        </div>
        <p className="min-w-0 flex-1 break-all font-mono text-sm text-muted">{report.source}</p>
        {!shared && (
          <div className="no-print flex flex-wrap gap-2">
            <button className="btn" onClick={exportMarkdown}>Export Markdown</button>
            <button className="btn" onClick={() => window.print()}>Save as PDF</button>
            <button className="btn" onClick={share}>Share link</button>
          </div>
        )}
      </header>
      {error && <p className="text-danger" role="alert">{error}</p>}
      {link && (
        <p className="panel break-all p-3 font-mono text-sm">
          Anyone with this link can read the report: <a className="underline" href={link}>{link}</a>
        </p>
      )}

      <section aria-label="Score breakdown">
        <h2 className="label mb-2">Breakdown</h2>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-1 font-mono text-sm sm:grid-cols-4">
          {Object.entries(report.breakdown).map(([area, value]) => (
            <div key={area} className="flex justify-between border-b border-line py-1">
              <dt className="text-muted">{area}</dt>
              <dd className={value < 70 ? 'text-danger' : ''}>{value}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section aria-label="Issues">
        <h2 className="label mb-2">{report.issues.length} issue{report.issues.length === 1 ? '' : 's'}</h2>
        {report.issues.length === 0 && <p>No issues found.</p>}
        <ol className="space-y-3">
          {report.issues.map((issue, index) => (
            <li key={index} className="panel p-3">
              <p className="font-mono text-sm">
                <span className={`font-semibold uppercase ${TONE[issue.severity]}`}>{issue.severity}</span>
                <span className="text-muted"> · {issue.category} · </span>
                <span className="break-all">{issue.file}:{issue.line}</span>
                <span className="text-muted"> · found by {issue.source}</span>
              </p>
              <p className="mt-1">{issue.explanation}</p>
              <p className="mt-1 text-muted"><span className="text-ink">Why it matters:</span> {issue.why}</p>
              <p className="mt-1 text-muted"><span className="text-ink">Suggested fix:</span> {issue.fix}</p>
            </li>
          ))}
        </ol>
      </section>

      {report.not_run.length > 0 && (
        <section>
          <h2 className="label mb-2">Checks that did not run</h2>
          <ul className="list-disc pl-5 text-muted">{report.not_run.map((item) => <li key={item}>{item}</li>)}</ul>
        </section>
      )}
    </article>
  )
}

export function SharedReview({ token }: { token: string }) {
  const [report, setReport] = useState<Report | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    api(`/shared/${token}`).then(setReport).catch((e) => setError(e.message))
  }, [token])
  return (
    <main className="mx-auto max-w-4xl px-4 py-6">
      <h1 className="mb-4 font-mono text-lg font-semibold">VoxAgent code review</h1>
      {error && <p className="text-danger" role="alert">{error}</p>}
      {report && <ReportView report={report} shared />}
    </main>
  )
}

export default function Reviews({ id }: { id?: string }) {
  const [list, setList] = useState<Report[]>([])
  const [report, setReport] = useState<Report | null>(null)
  const [code, setCode] = useState('')
  const [url, setUrl] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api('/reviews').then(setList).catch(() => {})
  }, [report])
  useEffect(() => {
    setReport(null)
    if (id) api(`/reviews/${id}`).then(setReport).catch((e) => setError(e.message))
  }, [id])

  async function run(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const body = file ? { upload_id: (await upload(file)).id } : url ? { github_url: url } : { code }
      const created = await post<Report>('/reviews', body)
      location.hash = `#/reviews/${created.id}`
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="mx-auto w-full max-w-4xl space-y-6 px-4 py-6">
      <h1 className="font-mono text-lg font-semibold">Code review</h1>
      {report ? (
        <>
          <a className="no-print underline" href="#/reviews">← New review</a>
          <ReportView report={report} />
        </>
      ) : (
        <>
          <form className="no-print space-y-3" onSubmit={run}>
            <div>
              <label className="label" htmlFor="code">Paste code</label>
              <textarea id="code" className="field h-40 font-mono text-sm" value={code} onChange={(e) => setCode(e.target.value)} />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label className="label" htmlFor="file">Or a file or zip</label>
                <input id="file" type="file" className="field" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
              </div>
              <div>
                <label className="label" htmlFor="url">Or a public GitHub URL</label>
                <input id="url" type="url" className="field" placeholder="https://github.com/owner/repo"
                  value={url} onChange={(e) => setUrl(e.target.value)} />
              </div>
            </div>
            <button className="btn btn-accent" disabled={busy || (!code.trim() && !file && !url)}>
              {busy ? 'Reviewing… this can take a minute' : 'Review'}
            </button>
            {error && <p className="text-danger" role="alert">{error}</p>}
          </form>
          {list.length > 0 && (
            <section>
              <h2 className="label mb-2">Past reviews</h2>
              <ul className="panel divide-y divide-line">
                {list.map((item) => (
                  <li key={item.id}>
                    <a className="flex min-h-11 items-center gap-3 px-3 font-mono text-sm hover:bg-surface" href={`#/reviews/${item.id}`}>
                      <span className="w-14">{item.score}/100</span>
                      <span className="min-w-0 flex-1 truncate">{item.source}</span>
                      <span className="text-muted">{new Date(item.created_at + 'Z').toLocaleDateString()}</span>
                    </a>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </main>
  )
}
