import { useEffect, useState } from 'react'
import { api, download, post, upload } from './api'
import {
  Download,
  Printer,
  Share2,
  GitBranch,
  UploadCloud,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Copy,
  Check,
  ArrowLeft,
  ShieldCheck,
  Sparkles,
  Clock,
  ChevronRight,
  AlertCircle,
  Code2,
} from 'lucide-react'

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

const SEVERITY_CONFIG: Record<
  string,
  { label: string; badge: string; icon: typeof AlertTriangle }
> = {
  critical: {
    label: 'Critical',
    badge: 'border-danger/40 bg-danger-subtle text-danger',
    icon: AlertCircle,
  },
  high: {
    label: 'High',
    badge: 'border-danger/30 bg-danger-subtle/70 text-danger',
    icon: AlertTriangle,
  },
  medium: {
    label: 'Medium',
    badge: 'border-warn/40 bg-warn-subtle text-warn',
    icon: AlertTriangle,
  },
  low: {
    label: 'Low',
    badge: 'border-line bg-surface text-muted',
    icon: CheckCircle2,
  },
}

function getGrade(score: number): { grade: string; color: string; message: string } {
  if (score >= 90) return { grade: 'A+', color: 'text-ok border-ok/40 bg-ok-subtle', message: 'Exceptional code quality and architectural integrity' }
  if (score >= 80) return { grade: 'A', color: 'text-ok border-ok/40 bg-ok-subtle', message: 'Production ready with minimal issues' }
  if (score >= 70) return { grade: 'B', color: 'text-warn border-warn/40 bg-warn-subtle', message: 'Good baseline, several improvements recommended' }
  if (score >= 60) return { grade: 'C', color: 'text-warn border-warn/40 bg-warn-subtle', message: 'Caution: Code contains maintainability or performance gaps' }
  return { grade: 'F', color: 'text-danger border-danger/40 bg-danger-subtle', message: 'Critical issues detected requiring immediate remediation' }
}

export function ReportView({ report, shared }: { report: Report; shared?: boolean }) {
  const [link, setLink] = useState('')
  const [copiedLink, setCopiedLink] = useState(false)
  const [error, setError] = useState('')
  const [filterSeverity, setFilterSeverity] = useState<string>('all')
  const [copiedFixIndex, setCopiedFixIndex] = useState<number | null>(null)

  const gradeInfo = getGrade(report.score)

  async function share() {
    try {
      const { token } = await post(`/reviews/${report.id}/share`)
      const url = `${location.origin}/#/shared/${token}`
      setLink(url)
      await navigator.clipboard?.writeText(url).catch(() => {})
      setCopiedLink(true)
      setTimeout(() => setCopiedLink(false), 3000)
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

  const copyFix = async (fix: string, idx: number) => {
    await navigator.clipboard.writeText(fix).catch(() => {})
    setCopiedFixIndex(idx)
    setTimeout(() => setCopiedFixIndex(null), 2000)
  }

  const filteredIssues = report.issues.filter(
    (issue) => filterSeverity === 'all' || issue.severity.toLowerCase() === filterSeverity
  )

  const severityCounts = report.issues.reduce<Record<string, number>>((acc, issue) => {
    const sev = issue.severity.toLowerCase()
    acc[sev] = (acc[sev] || 0) + 1
    return acc
  }, {})

  return (
    <article className="space-y-6">
      {/* Top Score Banner Card */}
      <div className="panel p-6 shadow-sm sm:p-8">
        <div className="flex flex-col gap-6 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-6">
            {/* Score Ring / Grade */}
            <div className="relative flex h-24 w-24 shrink-0 flex-col items-center justify-center rounded-2xl border border-line bg-surface p-2 shadow-inner">
              <span className="font-mono text-3xl font-extrabold tracking-tight text-ink">
                {report.score}
              </span>
              <span className="font-mono text-[10px] uppercase tracking-wider text-muted">/ 100</span>
              <span className={`absolute -right-2 -top-2 flex h-7 w-7 items-center justify-center rounded-lg border font-mono text-xs font-bold shadow-xs ${gradeInfo.color}`}>
                {gradeInfo.grade}
              </span>
            </div>

            <div>
              <div className="flex items-center gap-2">
                <span className="label">Quality Audit Report</span>
                <span className="font-mono text-xs text-muted">· {new Date(report.created_at + 'Z').toLocaleDateString()}</span>
              </div>
              <h2 className="mt-1 text-base font-semibold text-ink sm:text-lg">
                {gradeInfo.message}
              </h2>
              <p className="mt-1 break-all font-mono text-xs text-muted">
                Source: <span className="text-ink">{report.source}</span>
              </p>
            </div>
          </div>

          {!shared && (
            <div className="no-print flex flex-wrap items-center gap-2">
              <button className="btn h-9" onClick={exportMarkdown} title="Download Markdown report">
                <Download className="h-3.5 w-3.5" /> Markdown
              </button>
              <button className="btn h-9" onClick={() => window.print()} title="Print or save as PDF">
                <Printer className="h-3.5 w-3.5" /> Print / PDF
              </button>
              <button
                className={`btn h-9 ${copiedLink ? 'border-ok text-ok' : ''}`}
                onClick={share}
                title="Generate shareable URL"
              >
                {copiedLink ? <Check className="h-3.5 w-3.5 text-ok" /> : <Share2 className="h-3.5 w-3.5" />}
                {copiedLink ? 'Link Copied' : 'Share'}
              </button>
            </div>
          )}
        </div>

        {link && (
          <div className="mt-5 flex items-center justify-between rounded-xl border border-line bg-surface p-3 font-mono text-xs text-muted">
            <span className="truncate pr-3">Public share link: <span className="text-ink">{link}</span></span>
            <span className="shrink-0 text-[11px] text-ok">✓ Copied to clipboard</span>
          </div>
        )}

        {error && <p className="mt-4 text-xs text-danger" role="alert">{error}</p>}
      </div>

      {/* Category Breakdown Progress Grid */}
      <section aria-label="Score breakdown" className="panel p-6 shadow-xs">
        <h3 className="label mb-4 flex items-center gap-2">
          <ShieldCheck className="h-3.5 w-3.5 text-accent" /> Dimension Breakdown
        </h3>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Object.entries(report.breakdown).map(([area, value]) => {
            const isGood = value >= 80
            const isMedium = value >= 60 && value < 80
            const barColor = isGood ? 'bg-ok' : isMedium ? 'bg-warn' : 'bg-danger'
            const textColor = isGood ? 'text-ok' : isMedium ? 'text-warn' : 'text-danger'

            return (
              <div key={area} className="rounded-xl border border-line bg-surface/60 p-3.5">
                <div className="flex items-center justify-between font-mono text-xs">
                  <span className="font-medium text-muted capitalize">{area}</span>
                  <span className={`font-bold ${textColor}`}>{value}%</span>
                </div>
                <div className="mt-2.5 h-1.5 w-full overflow-hidden rounded-full bg-line/60">
                  <div
                    className={`h-full ${barColor} transition-all duration-300`}
                    style={{ width: `${Math.min(100, value)}%` }}
                  />
                </div>
              </div>
            )
          })}
        </div>
      </section>

      {/* Issues Section with Filters */}
      <section aria-label="Audit issues" className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <h3 className="font-mono text-sm font-bold text-ink">
              Identified Issues ({report.issues.length})
            </h3>
          </div>

          {/* Severity Filter Tabs */}
          <div className="flex rounded-xl border border-line bg-raised p-1 font-mono text-xs shadow-xs">
            <button
              onClick={() => setFilterSeverity('all')}
              className={`rounded-lg px-2.5 py-1 transition-colors ${
                filterSeverity === 'all' ? 'bg-surface font-semibold text-ink shadow-xs' : 'text-muted hover:text-ink'
              }`}
            >
              All ({report.issues.length})
            </button>
            {['critical', 'high', 'medium', 'low'].map((sev) => {
              const count = severityCounts[sev] || 0
              if (count === 0 && filterSeverity !== sev) return null
              return (
                <button
                  key={sev}
                  onClick={() => setFilterSeverity(sev)}
                  className={`rounded-lg px-2.5 py-1 capitalize transition-colors ${
                    filterSeverity === sev ? 'bg-surface font-semibold text-ink shadow-xs' : 'text-muted hover:text-ink'
                  }`}
                >
                  {sev} ({count})
                </button>
              )
            })}
          </div>
        </div>

        {filteredIssues.length === 0 ? (
          <div className="panel p-8 text-center font-mono text-xs text-muted">
            <CheckCircle2 className="mx-auto h-8 w-8 text-ok" />
            <p className="mt-2 text-sm text-ink font-semibold">No issues found</p>
            <p className="mt-1">
              {filterSeverity !== 'all' ? `No issues matching severity "${filterSeverity}".` : 'Clean code! No defects were detected.'}
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {filteredIssues.map((issue, index) => {
              const config = SEVERITY_CONFIG[issue.severity.toLowerCase()] || SEVERITY_CONFIG.low
              const Icon = config.icon

              return (
                <div key={index} className="panel p-4 shadow-xs sm:p-5">
                  {/* Issue Meta Header */}
                  <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line pb-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 font-mono text-[11px] font-bold uppercase tracking-wider ${config.badge}`}>
                        <Icon className="h-3 w-3" />
                        {issue.severity}
                      </span>
                      <span className="rounded-md border border-line bg-surface px-2 py-0.5 font-mono text-xs text-muted">
                        {issue.category}
                      </span>
                    </div>

                    <div className="flex items-center gap-2 font-mono text-xs text-muted">
                      <span className="rounded-md bg-surface px-2 py-0.5 text-ink font-medium">
                        {issue.file}:{issue.line}
                      </span>
                      <span className="text-[11px]">via {issue.source}</span>
                    </div>
                  </div>

                  {/* Issue Explanation */}
                  <div className="mt-3 text-sm text-ink font-medium leading-relaxed">
                    {issue.explanation}
                  </div>

                  {/* Why it Matters Callout */}
                  <div className="mt-3 rounded-xl border border-line/60 bg-surface/50 p-3 text-xs leading-relaxed text-muted">
                    <span className="font-semibold text-ink">Impact & Context: </span>
                    {issue.why}
                  </div>

                  {/* Suggested Fix Code Box */}
                  {issue.fix && (
                    <div className="mt-3 overflow-hidden rounded-xl border border-line bg-surface shadow-inner">
                      <div className="flex items-center justify-between border-b border-line bg-raised/70 px-3 py-1.5 font-mono text-[11px] text-muted">
                        <span className="font-semibold text-accent">Suggested Remediation</span>
                        <button
                          onClick={() => copyFix(issue.fix, index)}
                          className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-muted transition-colors hover:bg-surface hover:text-ink"
                          title="Copy fix code"
                        >
                          {copiedFixIndex === index ? (
                            <>
                              <Check className="h-3 w-3 text-ok" />
                              <span className="text-ok">Copied</span>
                            </>
                          ) : (
                            <>
                              <Copy className="h-3 w-3" />
                              <span>Copy</span>
                            </>
                          )}
                        </button>
                      </div>
                      <pre className="overflow-x-auto p-3 font-mono text-xs leading-relaxed text-ink">
                        <code>{issue.fix}</code>
                      </pre>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </section>

      {/* Checks That Did Not Run */}
      {report.not_run.length > 0 && (
        <section className="rounded-xl border border-line bg-surface/40 p-4 text-xs">
          <h4 className="label mb-2 flex items-center gap-1.5">
            <XCircle className="h-3.5 w-3.5 text-muted" /> Skipped / Inapplicable Analyzers
          </h4>
          <ul className="flex flex-wrap gap-2">
            {report.not_run.map((item) => (
              <li key={item} className="rounded-md border border-line bg-raised px-2 py-0.5 font-mono text-[11px] text-muted">
                {item}
              </li>
            ))}
          </ul>
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
    <main className="mx-auto max-w-4xl px-4 py-8">
      <div className="mb-6 flex items-center justify-between border-b border-line pb-4">
        <div>
          <h1 className="font-mono text-base font-bold text-ink">VoxAgent Shared Review</h1>
          <p className="text-xs text-muted">Publicly shared code audit snapshot</p>
        </div>
        <a href="#/" className="btn text-xs">
          Open VoxAgent
        </a>
      </div>

      {error && (
        <div className="panel border-danger/40 bg-danger-subtle p-4 text-xs text-danger" role="alert">
          {error}
        </div>
      )}

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
  const [inputTab, setInputTab] = useState<'code' | 'file' | 'git'>('code')
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
      const body =
        inputTab === 'file' && file
          ? { upload_id: (await upload(file)).id }
          : inputTab === 'git' && url
          ? { github_url: url }
          : { code }

      const created = await post<Report>('/reviews', body)
      location.hash = `#/reviews/${created.id}`
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const isSubmitDisabled =
    busy ||
    (inputTab === 'code' && !code.trim()) ||
    (inputTab === 'file' && !file) ||
    (inputTab === 'git' && !url.trim())

  return (
    <main className="mx-auto w-full max-w-4xl space-y-6 px-4 py-8">
      {report ? (
        <>
          <div className="no-print mb-4">
            <a
              className="inline-flex items-center gap-1.5 font-mono text-xs text-muted hover:text-ink transition-colors"
              href="#/reviews"
            >
              <ArrowLeft className="h-3.5 w-3.5" /> Back to Reviews
            </a>
          </div>
          <ReportView report={report} />
        </>
      ) : (
        <>
          {/* Header */}
          <div className="flex items-center justify-between border-b border-line pb-4">
            <div>
              <h1 className="font-mono text-lg font-bold text-ink">Automated Code Review</h1>
              <p className="text-xs text-muted">
                Run comprehensive static security, performance, AST, and maintainability checks.
              </p>
            </div>
            <span className="inline-flex items-center gap-1 rounded-full border border-accent/30 bg-accent-subtle px-2.5 py-1 font-mono text-[11px] font-semibold text-accent">
              <Sparkles className="h-3 w-3" /> Multi-Engine Audit
            </span>
          </div>

          {/* Review Submission Card */}
          <div className="panel p-6 shadow-sm">
            {/* Input Mode Tabs */}
            <div className="mb-5 flex rounded-xl border border-line bg-surface p-1">
              <button
                type="button"
                onClick={() => setInputTab('code')}
                className={`flex h-9 flex-1 items-center justify-center gap-2 rounded-lg font-mono text-xs font-semibold transition-all ${
                  inputTab === 'code' ? 'bg-raised text-ink shadow-xs' : 'text-muted hover:text-ink'
                }`}
              >
                <Code2 className="h-3.5 w-3.5" /> Paste Code
              </button>
              <button
                type="button"
                onClick={() => setInputTab('file')}
                className={`flex h-9 flex-1 items-center justify-center gap-2 rounded-lg font-mono text-xs font-semibold transition-all ${
                  inputTab === 'file' ? 'bg-raised text-ink shadow-xs' : 'text-muted hover:text-ink'
                }`}
              >
                <UploadCloud className="h-3.5 w-3.5" /> Upload File / Zip
              </button>
              <button
                type="button"
                onClick={() => setInputTab('git')}
                className={`flex h-9 flex-1 items-center justify-center gap-2 rounded-lg font-mono text-xs font-semibold transition-all ${
                  inputTab === 'git' ? 'bg-raised text-ink shadow-xs' : 'text-muted hover:text-ink'
                }`}
              >
                <GitBranch className="h-3.5 w-3.5" /> GitHub Repo
              </button>
            </div>

            <form className="no-print space-y-4" onSubmit={run}>
              {inputTab === 'code' && (
                <div>
                  <label className="label mb-1.5 block" htmlFor="code">Code Snippet</label>
                  <textarea
                    id="code"
                    className="field h-48 font-mono text-xs leading-relaxed"
                    placeholder="def execute_query(db, user_input):&#10;    # Paste your Python, TypeScript, or other code here&#10;    ..."
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                  />
                </div>
              )}

              {inputTab === 'file' && (
                <div>
                  <label className="label mb-1.5 block" htmlFor="file">Select File or Zip Archive</label>
                  <label className="flex h-36 w-full cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed border-line bg-surface/50 p-4 transition-colors hover:border-accent hover:bg-surface">
                    <UploadCloud className="h-8 w-8 text-muted" />
                    <span className="mt-2 font-mono text-xs font-medium text-ink">
                      {file ? file.name : 'Click to choose file or drag and drop'}
                    </span>
                    <span className="mt-0.5 text-[11px] text-muted">
                      Supports source files (.py, .js, .ts) or .zip bundles up to 5MB
                    </span>
                    <input
                      id="file"
                      type="file"
                      className="sr-only"
                      onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                    />
                  </label>
                </div>
              )}

              {inputTab === 'git' && (
                <div>
                  <label className="label mb-1.5 block" htmlFor="url">Public GitHub Repository</label>
                  <div className="relative flex items-center">
                    <GitBranch className="pointer-events-none absolute left-3.5 h-4 w-4 text-muted" />
                    <input
                      id="url"
                      type="url"
                      className="field h-11 pl-10 font-mono text-xs"
                      placeholder="https://github.com/fastapi/fastapi"
                      value={url}
                      onChange={(e) => setUrl(e.target.value)}
                    />
                  </div>
                  <p className="mt-1.5 text-xs text-muted">VoxAgent will shallow clone and analyze default branch files.</p>
                </div>
              )}

              {error && (
                <div className="flex items-center gap-2 rounded-xl border border-danger/40 bg-danger-subtle p-3 text-xs text-danger" role="alert">
                  <AlertCircle className="h-4 w-4 shrink-0" />
                  <p>{error}</p>
                </div>
              )}

              <button
                type="submit"
                className="btn btn-accent h-11 w-full text-xs font-bold shadow-sm justify-center"
                disabled={isSubmitDisabled}
              >
                {busy ? (
                  <span className="flex items-center gap-2 font-mono">
                    <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-surface border-t-transparent" />
                    Analyzing codebase… this may take 30–60 seconds
                  </span>
                ) : (
                  <span className="flex items-center gap-2 font-mono">
                    <Sparkles className="h-4 w-4" /> Start Quality Audit
                  </span>
                )}
              </button>
            </form>
          </div>

          {/* Past Reviews List */}
          {list.length > 0 && (
            <section className="space-y-3 pt-2">
              <h2 className="label flex items-center gap-1.5">
                <Clock className="h-3.5 w-3.5 text-accent" /> Review History ({list.length})
              </h2>
              <div className="panel divide-y divide-line overflow-hidden shadow-xs">
                {list.map((item) => {
                  const grade = getGrade(item.score)
                  return (
                    <a
                      key={item.id}
                      href={`#/reviews/${item.id}`}
                      className="flex items-center justify-between p-3.5 font-mono text-xs transition-colors hover:bg-surface"
                    >
                      <div className="flex min-w-0 items-center gap-3">
                        <span className={`inline-flex items-center justify-center rounded-lg border px-2 py-1 font-bold ${grade.color}`}>
                          {item.score}/100
                        </span>
                        <div className="min-w-0">
                          <p className="truncate font-semibold text-ink">{item.source}</p>
                          <p className="text-[11px] text-muted">{item.issues.length} issues identified</p>
                        </div>
                      </div>

                      <div className="flex items-center gap-3 text-muted">
                        <span>{new Date(item.created_at + 'Z').toLocaleDateString()}</span>
                        <ChevronRight className="h-4 w-4 text-muted" />
                      </div>
                    </a>
                  )
                })}
              </div>
            </section>
          )}
        </>
      )}
    </main>
  )
}
