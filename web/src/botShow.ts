/**
 * What the bots do on screen: spell headlines and market moves, build objects the server designed and rebuild
 * .glb models the user imports. The cards in BotDeck.tsx show the same things as text.
 */
import { BUILTIN, fromGlb, fromParts, fromPoints, fromText, ModelError, releaseShape, showShape, wrap, type Part } from './shapes'

export type Headline = { title: string; source: string; url: string }
export type Market = { symbol: string; name: string; region: string; price: number; currency: string; change_pct: number }
export type Briefing = { region: string; news: Record<string, Headline[]>; markets: Market[] }
export type Build = { name: string; source: 'ready-made' | 'designed' | 'imported' | 'real model'; detail: string }

export const SECTION_ORDER = ['topic', 'top', 'india', 'world', 'business', 'tech', 'science', 'sports']

/* ---------------------------------------------------------------- what the bots do */

let timers: number[] = []

/** Text that fits this screen: shorter lines on a phone, and never wider than the viewport can show. */
function layout(text: string): { lines: string[]; fit: number } {
  const stage = document.querySelector('[data-swarm-stage]')?.getBoundingClientRect()
  const width = stage?.width || innerWidth
  const narrow = width < 640
  // half the stage, in the swarm's world units: the engine draws x at x / 2.4 * 2.1 / aspect of the half window
  const fit = (width / innerWidth) * 0.9 * (2.4 / 2.1) * (innerWidth / Math.max(innerHeight, 1))
  return { lines: wrap(text, narrow ? 14 : 24, narrow ? 4 : 3), fit: Math.min(0.85, fit) }
}

/** Stop whatever the bots were showing (a news reel or a held object) and send them back to the page formation. */
export function releaseBots() {
  timers.forEach(clearTimeout)
  timers = []
  releaseShape()
}

function reel(frames: { lines: string[]; fit: number; hold: number }[]) {
  timers.forEach(clearTimeout)
  timers = []
  let at = 0
  for (const frame of frames) {
    timers.push(window.setTimeout(() => showShape({ points: fromText(frame.lines, undefined, undefined, frame.fit), steady: true, label: frame.lines.join(' ') }), at))
    at += frame.hold
  }
  timers.push(window.setTimeout(releaseShape, at))
}

export function arrow(change: number) {
  return change > 0 ? '▲' : change < 0 ? '▼' : '■'
}

export function marketLine(m: Market) {
  return `${m.name} ${arrow(m.change_pct)}${Math.abs(m.change_pct).toFixed(1)}%`
}

/** The bots spell the lead headline, then the user's key markets, then go back to the page formation. */
export function playNews(data: Briefing) {
  const lead = SECTION_ORDER.map((k) => data.news?.[k]?.[0]).find(Boolean)
  const frames: { lines: string[]; fit: number; hold: number }[] = []
  if (lead) frames.push({ ...layout(lead.title), hold: 8000 })
  if (data.markets?.length) frames.push({ lines: data.markets.slice(0, 3).map(marketLine), fit: layout('').fit, hold: 6500 })
  if (frames.length) reel(frames)
}

/** Spell one line of text with the bots for a while (a headline or a market the user picked). */
export function spell(text: string, hold = 9000) {
  reel([{ ...layout(text), hold }])
}

/** Build an object the server asked for: a ready-made one, or one designed from parts. Held until released. */
export function playBuild(event: { name: string; builtin?: string; parts?: Part[]; points?: number[]; credit?: string }): Build | null {
  if (event.points?.length) {
    timers.forEach(clearTimeout)
    timers = []
    showShape({ points: fromPoints(event.points), steady: false, label: event.name })
    return { name: event.name, source: 'real model', detail: buildCredit(event.credit) }
  }
  const parts = event.builtin ? BUILTIN[event.builtin] : event.parts
  if (!parts?.length) return null
  timers.forEach(clearTimeout)
  timers = []
  showShape({ points: fromParts(parts), steady: false, label: event.name })
  return event.builtin
    ? { name: event.name, source: 'ready-made', detail: `${parts.length} parts` }
    : { name: event.name, source: 'designed', detail: `designed from ${parts.length} parts` }
}

/** "File:Eiffel.stl" or its Commons page -> a short credit line. */
export const buildCredit = (credit = '') =>
  `scanned model from Wikimedia Commons: ${decodeURIComponent(credit.split('/').pop() ?? '').replace(/^File:/, '').replace(/_/g, ' ')}`

const MAX_MODEL_BYTES = 40 * 1024 * 1024

/** Import a .glb model the user picked or dropped: the bots rebuild it from its triangles, all in the browser. */
export async function importModel(file: File): Promise<Build> {
  if (file.size > MAX_MODEL_BYTES) throw new ModelError('That model is over 40 MB. Try a lighter export.')
  const { points, triangles } = fromGlb(await file.arrayBuffer())
  timers.forEach(clearTimeout)
  timers = []
  const name = file.name.replace(/\.glb$/i, '')
  showShape({ points, steady: false, label: name })
  return { name, source: 'imported', detail: `${triangles.toLocaleString()} triangles` }
}

export const isModel = (file: File) => /\.glb$/i.test(file.name)

export function botCount() {
  const n = Number(document.querySelector<HTMLCanvasElement>('.swarm-backdrop canvas')?.dataset.bots || 0)
  return n ? n.toLocaleString() : ''
}

