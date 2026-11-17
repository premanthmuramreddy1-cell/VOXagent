// Tiny Markdown parser for assistant replies. It returns data, never HTML, so a reply cannot inject markup.
export type Inline =
  | { t: 'text'; v: string }
  | { t: 'code'; v: string }
  | { t: 'b' | 'i'; c: Inline[] }
  | { t: 'a'; href: string; c: Inline[] }
export type Block =
  | { t: 'p'; c: Inline[] }
  | { t: 'h'; level: 1 | 2 | 3; c: Inline[] }
  | { t: 'ul'; items: Inline[][] }
  | { t: 'ol'; start: number; items: Inline[][] }
  | { t: 'pre'; lang: string; v: string }

const INLINE = /`([^`\n]+)`|\*\*([^*\n]+?)\*\*|\*([^*\s][^*\n]*?)\*|\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)|(https?:\/\/[^\s<>()]+)/g

export function inline(text: string): Inline[] {
  const out: Inline[] = []
  let at = 0
  for (const m of text.matchAll(INLINE)) {
    if (m.index > at) out.push({ t: 'text', v: text.slice(at, m.index) })
    let used = m[0]
    if (m[1] !== undefined) out.push({ t: 'code', v: m[1] })
    else if (m[2] !== undefined) out.push({ t: 'b', c: inline(m[2]) })
    else if (m[3] !== undefined) out.push({ t: 'i', c: inline(m[3]) })
    else if (m[4] !== undefined) out.push({ t: 'a', href: m[5], c: inline(m[4]) })
    else {
      used = m[6].replace(/[.,;:!?'"]+$/, '') // a sentence's full stop is not part of its link
      out.push({ t: 'a', href: used, c: [{ t: 'text', v: used }] })
    }
    at = m.index + used.length
  }
  if (at < text.length) out.push({ t: 'text', v: text.slice(at) })
  return out
}

const BULLET = /^\s*[-*•]\s+(.*)$/
const NUMBER = /^\s*(\d{1,9})[.)]\s+(.*)$/
const HEADING = /^(#{1,3})\s+(.*)$/

export function parse(text: string): Block[] {
  const lines = text.replace(/\r\n?/g, '\n').split('\n')
  const blocks: Block[] = []
  let para: string[] = []
  const flush = () => {
    if (para.length) blocks.push({ t: 'p', c: inline(para.join('\n')) })
    para = []
  }
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    const fence = line.match(/^\s*```\s*([\w+-]*)\s*$/)
    if (fence) {
      flush()
      const code: string[] = []
      while (++i < lines.length && !/^\s*```\s*$/.test(lines[i])) code.push(lines[i])
      blocks.push({ t: 'pre', lang: fence[1], v: code.join('\n') }) // an unclosed fence (still streaming) runs to the end
      continue
    }
    const heading = line.match(HEADING)
    if (heading) {
      flush()
      blocks.push({ t: 'h', level: heading[1].length as 1 | 2 | 3, c: inline(heading[2]) })
      continue
    }
    const bullet = line.match(BULLET)
    const number = line.match(NUMBER)
    if (bullet || number) {
      flush()
      const kind = bullet ? 'ul' : 'ol'
      const last = blocks[blocks.length - 1]
      const item = inline((bullet ? bullet[1] : number![2]).trim())
      if (last && last.t === kind) last.items.push(item)
      else if (kind === 'ul') blocks.push({ t: 'ul', items: [item] })
      else blocks.push({ t: 'ol', start: Number(number![1]), items: [item] })
      continue
    }
    if (!line.trim()) flush()
    else para.push(line)
  }
  flush()
  return blocks
}
