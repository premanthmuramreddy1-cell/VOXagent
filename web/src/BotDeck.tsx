import { useEffect, useMemo, useState } from 'react'
import { Box, ExternalLink, Newspaper, Sparkles, TrendingDown, TrendingUp, Minus, X } from 'lucide-react'
import { botCount, marketLine, releaseBots, SECTION_ORDER, spell, type Briefing, type Build, type Market } from './botShow'

const SECTION_LABEL: Record<string, string> = {
  topic: 'Topic', top: 'Top', india: 'India', world: 'World', business: 'Business', tech: 'Tech', science: 'Science', sports: 'Sports',
}
/* ---------------------------------------------------------------- cards */

/** Text that resolves out of noise, the way the bots resolve into it. Plain text under reduced motion. */
function Decode({ text, delay = 0 }: { text: string; delay?: number }) {
  const [still] = useState(() => matchMedia('(prefers-reduced-motion: reduce)').matches)
  const [shown, setShown] = useState('')
  useEffect(() => {
    if (still) return
    const glyphs = '01<>/[]{}#=+*'
    let raf = 0
    const start = performance.now() + delay
    const step = (now: number) => {
      const done = Math.max(0, Math.min(1, (now - start) / 650))
      const fixed = Math.floor(done * text.length)
      setShown(text.slice(0, fixed) + Array.from(text.slice(fixed, fixed + 6), (c) => (c === ' ' ? ' ' : glyphs[(Math.random() * glyphs.length) | 0])).join(''))
      if (done < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [text, delay, still])
  return <>{still ? text : shown}</>
}

function money(m: Market) {
  const digits = m.price >= 1000 ? 0 : 2
  return m.price.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export function NewsDeck({ data }: { data: Briefing }) {
  const sections = useMemo(() => SECTION_ORDER.filter((k) => data.news?.[k]?.length), [data])
  const [tab, setTab] = useState(sections[0] ?? '')
  const items = data.news?.[tab] ?? []
  const bots = useMemo(() => botCount(), [])
  return (
    <section className="deck overflow-hidden rounded-xl border border-line bg-raised" aria-label="Live briefing">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-3.5 py-2.5">
        <Newspaper className="h-4 w-4 text-accent" aria-hidden="true" />
        <span className="font-mono text-xs font-bold uppercase tracking-wider text-ink">Live briefing</span>
        {data.region && <span className="chip font-mono text-[11px]">{data.region}</span>}
        <span className="ml-auto font-mono text-[11px] text-muted">{bots ? `decoded by ${bots} bots` : 'decoded by the swarm'}</span>
      </header>

      {data.markets?.length > 0 && (
        <div className="deck-markets grid grid-cols-2 gap-px bg-line sm:grid-cols-3" role="list" aria-label="Markets">
          {data.markets.map((m) => {
            const Trend = m.change_pct > 0 ? TrendingUp : m.change_pct < 0 ? TrendingDown : Minus
            const tone = m.change_pct > 0 ? 'text-ok' : m.change_pct < 0 ? 'text-danger' : 'text-muted'
            return (
              <button key={m.symbol} type="button" role="listitem" onClick={() => spell(`${m.name} ${money(m)} ${marketLine(m).split(' ').pop()}`)}
                className="flex min-h-14 flex-col items-start gap-0.5 bg-raised px-3 py-2 text-left transition-colors hover:bg-raised-hover focus-visible:outline-2 focus-visible:outline-accent"
                title="Show this with the bots">
                <span className="font-mono text-[11px] uppercase tracking-wide text-muted">{m.name}</span>
                <span className="font-mono text-sm font-semibold tabular-nums text-ink">{money(m)}{m.currency && m.currency !== 'USD' && m.region !== 'IN' ? ` ${m.currency}` : ''}</span>
                <span className={`inline-flex items-center gap-1 font-mono text-xs tabular-nums ${tone}`}>
                  <Trend className="h-3 w-3" aria-hidden="true" />
                  {m.change_pct > 0 ? '+' : m.change_pct < 0 ? '−' : ''}{Math.abs(m.change_pct).toFixed(2)}%
                  <span className="sr-only">{m.change_pct > 0 ? 'up' : m.change_pct < 0 ? 'down' : 'flat'}</span>
                </span>
              </button>
            )
          })}
        </div>
      )}

      {sections.length > 0 && (
        <>
          <div className="flex gap-1 overflow-x-auto border-y border-line px-2 py-1.5" role="tablist" aria-label="News sections">
            {sections.map((k) => (
              <button key={k} type="button" role="tab" aria-selected={k === tab} onClick={() => setTab(k)}
                className={`min-h-9 shrink-0 rounded-lg px-2.5 font-mono text-xs transition-colors ${k === tab ? 'bg-accent-subtle text-accent' : 'text-muted hover:text-ink'}`}>
                {SECTION_LABEL[k] ?? k}
              </button>
            ))}
          </div>
          <ol className="divide-y divide-line" role="tabpanel" aria-label={`${SECTION_LABEL[tab] ?? tab} headlines`}>
            {items.map((h, i) => (
              <li key={h.url || h.title} className="group flex items-start gap-3 px-3.5 py-2.5">
                <span className="mt-0.5 w-6 shrink-0 font-mono text-[11px] tabular-nums text-muted">{String(i + 1).padStart(2, '0')}</span>
                <div className="min-w-0 flex-1">
                  <a href={h.url} target="_blank" rel="noopener noreferrer" className="text-sm leading-snug text-ink hover:text-accent">
                    <Decode text={h.title} delay={i * 70} />
                  </a>
                  {h.source && <div className="mt-0.5 font-mono text-[11px] text-muted">{h.source}</div>}
                </div>
                <button type="button" onClick={() => spell(h.title)} title="Show this headline with the bots"
                  className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-muted transition-colors hover:bg-accent-subtle hover:text-accent">
                  <Sparkles className="h-4 w-4" aria-hidden="true" />
                  <span className="sr-only">Show with the bots</span>
                </button>
                {h.url && <ExternalLink className="mt-1 hidden h-3.5 w-3.5 shrink-0 text-muted sm:block" aria-hidden="true" />}
              </li>
            ))}
          </ol>
        </>
      )}
      <footer className="flex items-center justify-between gap-2 border-t border-line px-3.5 py-2 font-mono text-[11px] text-muted">
        <span>Google News · Yahoo Finance</span>
        <button type="button" onClick={releaseBots} className="min-h-9 rounded-lg px-2 hover:text-ink">Release bots</button>
      </footer>
    </section>
  )
}

const SOURCE_TONE: Record<Build['source'], string> = { 'ready-made': 'text-accent', designed: 'text-warn', imported: 'text-ok', 'real model': 'text-ok' }

export function BuildCard({ build }: { build: Build }) {
  const bots = useMemo(() => botCount(), [])
  return (
    <div className="deck flex flex-wrap items-center gap-3 rounded-xl border border-line bg-raised px-3.5 py-3">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent-subtle text-accent">
        <Box className="h-4 w-4" aria-hidden="true" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate font-mono text-sm font-semibold capitalize text-ink">{build.name}</div>
        <div className="font-mono text-[11px] text-muted">
          <span className={SOURCE_TONE[build.source]}>{build.source}</span> · {build.detail}{bots ? ` · ${bots} bots` : ''}
        </div>
      </div>
      <button type="button" onClick={releaseBots}
        className="flex min-h-9 items-center gap-1.5 rounded-lg border border-line px-2.5 font-mono text-xs text-muted transition-colors hover:border-accent hover:text-ink">
        <X className="h-3.5 w-3.5" aria-hidden="true" /> Release
      </button>
    </div>
  )
}
