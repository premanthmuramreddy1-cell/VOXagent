// Self-check for markdown.ts: replies render as data, links stay http(s), and streaming text never breaks.
import assert from 'node:assert/strict'

const { parse, inline } = await import('../src/markdown.ts')

assert.deepEqual(inline('plain'), [{ t: 'text', v: 'plain' }])
assert.deepEqual(inline('use `npm i` **now**'), [
  { t: 'text', v: 'use ' }, { t: 'code', v: 'npm i' }, { t: 'text', v: ' ' }, { t: 'b', c: [{ t: 'text', v: 'now' }] },
])
assert.deepEqual(inline('see https://x.dev/a.'), [
  { t: 'text', v: 'see ' }, { t: 'a', href: 'https://x.dev/a', c: [{ t: 'text', v: 'https://x.dev/a' }] }, { t: 'text', v: '.' },
])
assert.deepEqual(inline('[docs](https://x.dev)'), [{ t: 'a', href: 'https://x.dev', c: [{ t: 'text', v: 'docs' }] }])
assert.equal(inline('[bad](javascript:alert(1))').some((n) => n.t === 'a'), false, 'only http(s) links become links')
assert.deepEqual(inline('2 * 3 * 4'), [{ t: 'text', v: '2 * 3 * 4' }], 'spaced asterisks are maths, not italics')
assert.deepEqual(inline('<img src=x onerror=alert(1)>'), [{ t: 'text', v: '<img src=x onerror=alert(1)>' }])

const blocks = parse('# Plan\nFirst line\nsecond line\n\n- one\n- **two**\n\n1. a\n2. b\n\n```py\nprint(1)\n```')
assert.deepEqual(blocks.map((b) => b.t), ['h', 'p', 'ul', 'ol', 'pre'])
assert.deepEqual(blocks[1], { t: 'p', c: [{ t: 'text', v: 'First line\nsecond line' }] })
assert.equal(blocks[2].t === 'ul' && blocks[2].items.length, 2)
assert.deepEqual(blocks[4], { t: 'pre', lang: 'py', v: 'print(1)' })
assert.deepEqual(parse('```js\nconst a = 1'), [{ t: 'pre', lang: 'js', v: 'const a = 1' }], 'unclosed fence while streaming')
assert.deepEqual(parse(''), [])
assert.deepEqual(parse('3. third\n4. fourth').map((b) => b.t === 'ol' && b.start), [3])
console.log('markdown ok: blocks, inline, safe links, streaming fences')
