import { ExternalLink } from 'lucide-react'

type Tool = { skill: string; state: 'running' | 'ok' | 'failed'; data?: any; error?: string }
type Source = { n?: number; title: string; url: string }

const SHOWN = 6

function host(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

/** Every page the answer was built from: research reports keep their [n] numbers, search and reads are listed after. */
function sourcesOf(tools: Tool[]): Source[] {
  const seen = new Map<string, Source>()
  for (const tool of tools) {
    const data = tool.data ?? {}
    const found: Source[] = [
      ...(Array.isArray(data.sources) ? data.sources : []),
      ...(Array.isArray(data.results) ? data.results : []),
      ...(typeof data.url === 'string' && tool.skill === 'web_fetch' ? [{ title: data.title, url: data.url }] : []),
    ]
    for (const source of found) {
      if (typeof source?.url === 'string' && /^https?:\/\//.test(source.url) && !seen.has(source.url)) {
        seen.set(source.url, { n: source.n, title: String(source.title || host(source.url)), url: source.url })
      }
    }
  }
  return [...seen.values()]
}

function Item({ source }: { source: Source }) {
  return (
    <li className="flex min-w-0 items-baseline gap-2">
      {source.n !== undefined && <span className="shrink-0 font-mono text-muted">[{source.n}]</span>}
      <a className="inline-flex min-w-0 items-baseline gap-1 text-ink hover:text-accent hover:underline"
         href={source.url} target="_blank" rel="noopener noreferrer">
        <span className="truncate">{source.title}</span>
        <span className="shrink-0 font-mono text-[11px] text-muted">{host(source.url)}</span>
        <ExternalLink className="h-2.5 w-2.5 shrink-0 self-center text-muted" aria-hidden="true" />
      </a>
    </li>
  )
}

/** The links behind an answer, shown under it so every fact can be checked at its source. */
export default function Sources({ tools }: { tools: Tool[] }) {
  const sources = sourcesOf(tools)
  if (!sources.length) return null
  return (
    <section aria-label="Sources" className="rounded-xl border border-line bg-surface px-3.5 py-2.5 text-xs">
      <h3 className="label mb-1.5">Sources · {sources.length}</h3>
      <ol className="space-y-1">
        {sources.slice(0, SHOWN).map((s) => <Item key={s.url} source={s} />)}
      </ol>
      {sources.length > SHOWN && (
        <details className="mt-1.5">
          <summary className="cursor-pointer text-muted hover:text-ink">{sources.length - SHOWN} more</summary>
          <ol className="mt-1 space-y-1">
            {sources.slice(SHOWN).map((s) => <Item key={s.url} source={s} />)}
          </ol>
        </details>
      )}
    </section>
  )
}
