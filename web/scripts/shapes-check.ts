// Self-check for src/shapes.ts. Run: npm run check:shapes (plain Node, no test framework needed).
import assert from 'node:assert/strict'
import { BUILTIN, fromGlb, fromParts, fromPoints, ModelError, normalize, SHAPE_N, wrap } from '../src/shapes.ts'

const extent = (p: Float32Array) => {
  let lo = Infinity, hi = -Infinity
  for (const v of p) { lo = Math.min(lo, v); hi = Math.max(hi, v) }
  return [lo, hi]
}

// every built-in fills all points, stays finite and fits the frame
for (const [name, parts] of Object.entries(BUILTIN)) {
  const pts = fromParts(parts)
  assert.equal(pts.length, SHAPE_N * 3, name)
  assert.ok(pts.every(Number.isFinite), `${name} has non-finite points`)
  const [lo, hi] = extent(pts)
  assert.ok(lo >= -0.781 && hi <= 0.781 && hi - lo > 1, `${name} does not fit the frame: ${lo}..${hi}`)
}

// same input, same points (the swarm does not jitter between rebuilds); bad parts are skipped, not fatal
assert.deepEqual(fromParts(BUILTIN.cup, 300), fromParts(BUILTIN.cup, 300))
assert.ok(fromParts([{ kind: 'box', at: [0, 0, 0], size: [NaN, 1, 1] }], 30).every((v) => v === 0))
assert.ok(fromParts([], 30).every((v) => v === 0))
assert.deepEqual(Array.from(normalize(new Float32Array([1, 1, 1, 1, 1, 1]))), [1, 1, 1, 1, 1, 1]) // flat input left alone
// server-sampled real models: grown to the swarm size, framed, bad numbers and an empty list are safe
const grown = fromPoints([1, 0, 0, -1, 0, 0, 0, 2, NaN], 90)
assert.equal(grown.length, 270)
assert.ok(grown.every(Number.isFinite) && Math.max(...grown.map(Math.abs)) <= 0.79)
assert.ok(fromPoints([], 10).every((v) => v === 0))

// a minimal .glb: one triangle moved by its node's translation
function glb(json: object, bin: ArrayBuffer): ArrayBuffer {
  const pad = (n: number) => (n + 3) & ~3
  const text = new TextEncoder().encode(JSON.stringify(json))
  const jsonLen = pad(text.length), binLen = pad(bin.byteLength)
  const out = new ArrayBuffer(12 + 8 + jsonLen + 8 + binLen)
  const v = new DataView(out)
  v.setUint32(0, 0x46546c67, true); v.setUint32(4, 2, true); v.setUint32(8, out.byteLength, true)
  v.setUint32(12, jsonLen, true); v.setUint32(16, 0x4e4f534a, true)
  new Uint8Array(out, 20, jsonLen).fill(0x20).set(text)
  v.setUint32(20 + jsonLen, binLen, true); v.setUint32(24 + jsonLen, 0x004e4942, true)
  new Uint8Array(out, 28 + jsonLen).set(new Uint8Array(bin))
  return out
}
const tri = new Float32Array([0, 0, 0, 2, 0, 0, 0, 2, 0]).buffer
const model = {
  asset: { version: '2.0' }, scene: 0, scenes: [{ nodes: [0] }],
  nodes: [{ mesh: 0, translation: [5, 0, 0] }],
  meshes: [{ primitives: [{ attributes: { POSITION: 0 } }] }],
  accessors: [{ bufferView: 0, componentType: 5126, count: 3, type: 'VEC3' }],
  bufferViews: [{ byteOffset: 0, byteLength: 36 }],
}
const imported = fromGlb(glb(model, tri), 500)
assert.equal(imported.triangles, 1)
assert.ok(imported.points.every(Number.isFinite))
for (let i = 0; i < 500; i++) assert.ok(Math.abs(imported.points[i * 3 + 2]) < 1e-6, 'a flat triangle stays flat')

const fails = (buf: ArrayBuffer, why: RegExp) => assert.throws(() => fromGlb(buf, 10), (e: unknown) => e instanceof ModelError && why.test((e as Error).message))
fails(new ArrayBuffer(40), /not a \.glb/)
fails(glb({ ...model, extensionsRequired: ['KHR_draco_mesh_compression'] }, tri), /Compressed/)
fails(glb({ ...model, accessors: [{ ...model.accessors[0], count: 300 }] }, tri), /out of range/)
fails(glb({ ...model, meshes: [] }, tri), /no triangles/)
fails(glb(model, tri).slice(0, 60), /cut short|no geometry/)

// headlines wrap into at most three lines and say when they were cut
assert.deepEqual(wrap('Monsoon reaches Kerala early this year', 20, 3), ['Monsoon reaches', 'Kerala early this', 'year'])
const cut = wrap('one two three four five six seven eight nine ten eleven twelve', 10, 2)
assert.equal(cut.length, 2)
assert.ok(cut[1].endsWith('…') && cut.every((l) => l.length <= 10), JSON.stringify(cut))
assert.ok(wrap('x'.repeat(80), 12, 3).every((l) => l.length <= 12))
assert.deepEqual(wrap('   ', 10, 3), [])
console.log(`shapes ok: ${Object.keys(BUILTIN).length} built-ins, glb import, wrap`)
