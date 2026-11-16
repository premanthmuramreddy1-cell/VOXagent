/**
 * Shapes the nanobot swarm can build: ready-made objects, objects the model designs from simple parts, imported
 * .glb models and text. Every source ends as SHAPE_N points (x, y, z) that the swarm engine uploads to the GPU, so
 * the swarm can morph from anything to anything. Pure and deterministic, so it runs (and is checked) in Node too.
 */

export const SHAPE_SIDE = 256
export const SHAPE_N = SHAPE_SIDE * SHAPE_SIDE

export type Vec3 = [number, number, number]
export type Part = { kind: 'sphere' | 'box' | 'cylinder' | 'cone' | 'torus'; at: Vec3; size: Vec3; turn?: Vec3 }
export type ShapeDetail = { points: Float32Array; steady: boolean; label: string }

const sphere = (at: Vec3, d: number | Vec3): Part => ({ kind: 'sphere', at, size: typeof d === 'number' ? [d, d, d] : d })
const box = (at: Vec3, size: Vec3, turn?: Vec3): Part => ({ kind: 'box', at, size, turn })
const cyl = (at: Vec3, size: Vec3, turn?: Vec3): Part => ({ kind: 'cylinder', at, size, turn })
const cone = (at: Vec3, size: Vec3, turn?: Vec3): Part => ({ kind: 'cone', at, size, turn })
const ring = (at: Vec3, diameter: number, tube: number, turn?: Vec3): Part => ({ kind: 'torus', at, size: [diameter, tube, diameter], turn })

/** The objects the bots know by heart. Kept in step with BUILTIN in backend/app/skills/build_tools.py. */
export const BUILTIN: Record<string, Part[]> = {
  bottle: [cyl([0, -0.25, 0], [0.5, 1, 0.5]), cone([0, 0.37, 0], [0.5, 0.25, 0.5]), cyl([0, 0.62, 0], [0.18, 0.32, 0.18]), cyl([0, 0.82, 0], [0.21, 0.08, 0.21])],
  cup: [cyl([0, 0, 0], [0.7, 0.7, 0.7]), ring([0.42, 0.02, 0], 0.34, 0.07, [90, 0, 0]), cyl([0, -0.38, 0], [1.1, 0.04, 1.1])],
  guitar: [sphere([0, -0.45, 0], [0.78, 0.66, 0.22]), sphere([0, -0.05, 0], [0.6, 0.5, 0.2]), ring([0, -0.18, 0.11], 0.2, 0.03, [90, 0, 0]),
    box([0, 0.5, 0], [0.1, 0.82, 0.06]), box([0, 0.97, 0], [0.17, 0.18, 0.05]), box([0, -0.6, 0.1], [0.3, 0.05, 0.03])],
  rocket: [cyl([0, 0, 0], [0.35, 1.1, 0.35]), cone([0, 0.75, 0], [0.35, 0.4, 0.35]), box([0.25, -0.48, 0], [0.2, 0.32, 0.03]),
    box([-0.25, -0.48, 0], [0.2, 0.32, 0.03]), box([0, -0.48, 0.25], [0.03, 0.32, 0.2]), box([0, -0.48, -0.25], [0.03, 0.32, 0.2]),
    cone([0, -0.75, 0], [0.25, 0.32, 0.25], [180, 0, 0]), ring([0, 0.2, 0.175], 0.13, 0.025, [90, 0, 0])],
  plane: [cyl([0, 0, 0], [0.22, 1.6, 0.22], [0, 0, 90]), sphere([0.8, 0, 0], 0.22), box([0.05, 0, 0], [0.38, 0.03, 1.7]),
    box([-0.72, 0.2, 0], [0.22, 0.32, 0.03]), box([-0.74, 0.02, 0], [0.18, 0.03, 0.6])],
  car: [box([0, -0.15, 0], [1.6, 0.3, 0.7]), box([-0.05, 0.13, 0], [0.82, 0.28, 0.62]), ring([0.5, -0.32, 0.36], 0.3, 0.1, [90, 0, 0]),
    ring([-0.5, -0.32, 0.36], 0.3, 0.1, [90, 0, 0]), ring([0.5, -0.32, -0.36], 0.3, 0.1, [90, 0, 0]), ring([-0.5, -0.32, -0.36], 0.3, 0.1, [90, 0, 0]),
    sphere([0.8, -0.12, 0.22], 0.08), sphere([0.8, -0.12, -0.22], 0.08)],
  house: [box([0, -0.25, 0], [1.2, 0.8, 0.9]), cone([0, 0.42, 0], [1.5, 0.55, 1.2]), box([0, -0.45, 0.455], [0.22, 0.4, 0.02]),
    box([0.36, -0.12, 0.455], [0.22, 0.22, 0.02]), box([-0.36, -0.12, 0.455], [0.22, 0.22, 0.02]), box([0.36, 0.52, 0], [0.14, 0.32, 0.14])],
  tree: [cyl([0, -0.6, 0], [0.18, 0.6, 0.18]), cone([0, -0.12, 0], [1.05, 0.6, 1.05]), cone([0, 0.24, 0], [0.82, 0.55, 0.82]), cone([0, 0.56, 0], [0.56, 0.45, 0.56])],
  robot: [box([0, 0.62, 0], [0.4, 0.32, 0.34]), sphere([0.09, 0.65, 0.18], 0.07), sphere([-0.09, 0.65, 0.18], 0.07), cyl([0, 0.86, 0], [0.03, 0.16, 0.03]),
    sphere([0, 0.95, 0], 0.07), box([0, 0.15, 0], [0.6, 0.6, 0.38]), cyl([0.42, 0.15, 0], [0.12, 0.56, 0.12]), cyl([-0.42, 0.15, 0], [0.12, 0.56, 0.12]),
    cyl([0.15, -0.43, 0], [0.15, 0.56, 0.15]), cyl([-0.15, -0.43, 0], [0.15, 0.56, 0.15])],
  atom: [sphere([0, 0, 0], 0.24), ring([0, 0, 0], 1.5, 0.02, [90, 0, 0]), ring([0, 0, 0], 1.5, 0.02, [90, 60, 0]), ring([0, 0, 0], 1.5, 0.02, [90, -60, 0]),
    sphere([0.75, 0, 0], 0.08), sphere([-0.375, 0, 0.65], 0.08), sphere([0, -0.75, 0], 0.08)],
  'solar system': [sphere([0, 0, 0], 0.36), ring([0, 0, 0], 0.8, 0.01), ring([0, 0, 0], 1.25, 0.01), ring([0, 0, 0], 1.7, 0.01),
    sphere([0.4, 0, 0], 0.07), sphere([0, 0, 0.625], 0.11), sphere([-0.85, 0, 0], 0.09), ring([-0.85, 0, 0], 0.22, 0.012, [20, 0, 0])],
  heart: [sphere([0.22, 0.18, 0], [0.56, 0.56, 0.4]), sphere([-0.22, 0.18, 0], [0.56, 0.56, 0.4]), cone([0, -0.26, 0], [0.92, 0.72, 0.4], [180, 0, 0])],
  chair: [box([0, -0.05, 0], [0.8, 0.08, 0.8]), box([0, 0.45, -0.36], [0.8, 0.9, 0.08]), cyl([0.34, -0.45, 0.34], [0.07, 0.8, 0.07]),
    cyl([-0.34, -0.45, 0.34], [0.07, 0.8, 0.07]), cyl([0.34, -0.45, -0.34], [0.07, 0.8, 0.07]), cyl([-0.34, -0.45, -0.34], [0.07, 0.8, 0.07])],
  globe: [sphere([0, 0.15, 0], 1), ring([0, 0.15, 0], 1.16, 0.02, [90, 0, 23]), cyl([0, -0.55, 0], [0.08, 0.36, 0.08]), cyl([0, -0.76, 0], [0.62, 0.05, 0.62])],
}

/** mulberry32: a tiny seeded generator, so the same shape always gets the same points. */
function rng(seed: number) {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function area(p: Part): number {
  const [x, y, z] = p.size.map((v) => Math.max(v, 0.001))
  const rx = x / 2, rz = z / 2
  switch (p.kind) {
    case 'sphere': return 4 * Math.PI * Math.pow(((x * y) ** 1.6 + (x * z) ** 1.6 + (y * z) ** 1.6) / 3, 1 / 1.6) / 4
    case 'box': return 2 * (x * y + x * z + y * z)
    case 'cylinder': return Math.PI * (rx + rz) * y + 2 * Math.PI * rx * rz
    case 'cone': return Math.PI * (rx + rz) / 2 * Math.hypot((rx + rz) / 2, y) + Math.PI * rx * rz
    case 'torus': return 4 * Math.PI * Math.PI * (x / 2) * (y / 2)
  }
}

/** One point on the surface of a part, in its own frame (centred at 0, y up). */
function onSurface(p: Part, r: () => number): Vec3 {
  const [x, y, z] = p.size
  const u = r(), v = r()
  switch (p.kind) {
    case 'sphere': {
      const phi = Math.acos(1 - 2 * u), th = v * Math.PI * 2
      return [Math.sin(phi) * Math.cos(th) * x / 2, Math.cos(phi) * y / 2, Math.sin(phi) * Math.sin(th) * z / 2]
    }
    case 'box': {
      const faces = [y * z, y * z, x * z, x * z, x * y, x * y]
      let pick = r() * faces.reduce((a, b) => a + b), f = 0
      while (pick > faces[f] && f < 5) pick -= faces[f++]
      const a = u - 0.5, b = v - 0.5, s = f % 2 ? -0.5 : 0.5
      if (f < 2) return [s * x, a * y, b * z]
      if (f < 4) return [a * x, s * y, b * z]
      return [a * x, b * y, s * z]
    }
    case 'cylinder': {
      const th = u * Math.PI * 2, side = Math.PI * (x + z) / 2 * y, cap = Math.PI * x * z / 4
      if (r() * (side + 2 * cap) < side) return [Math.cos(th) * x / 2, (v - 0.5) * y, Math.sin(th) * z / 2]
      const rad = Math.sqrt(v) / 2
      return [Math.cos(th) * rad * x, r() < 0.5 ? y / 2 : -y / 2, Math.sin(th) * rad * z]
    }
    case 'cone': {
      const th = u * Math.PI * 2
      if (r() < 0.78) {
        const h = 1 - Math.sqrt(v) // more points near the wide base, where there is more surface
        return [Math.cos(th) * (1 - h) * x / 2, (h - 0.5) * y, Math.sin(th) * (1 - h) * z / 2]
      }
      const rad = Math.sqrt(v) / 2
      return [Math.cos(th) * rad * x, -y / 2, Math.sin(th) * rad * z]
    }
    case 'torus': {
      const a = u * Math.PI * 2, b = v * Math.PI * 2, big = x / 2, tube = y / 2
      return [(big + tube * Math.cos(b)) * Math.cos(a), tube * Math.sin(b), (big + tube * Math.cos(b)) * Math.sin(a)]
    }
  }
}

function turn(p: Vec3, deg: Vec3 = [0, 0, 0]): Vec3 {
  let [x, y, z] = p
  const [ax, ay, az] = deg.map((d) => (d * Math.PI) / 180)
  ;[y, z] = [y * Math.cos(ax) - z * Math.sin(ax), y * Math.sin(ax) + z * Math.cos(ax)]
  ;[x, z] = [x * Math.cos(ay) + z * Math.sin(ay), -x * Math.sin(ay) + z * Math.cos(ay)]
  ;[x, y] = [x * Math.cos(az) - y * Math.sin(az), x * Math.sin(az) + y * Math.cos(az)]
  return [x, y, z]
}

/** Centre the points and scale the largest extent to `fit`, in place. Empty or flat input is left as it is. */
export function normalize(points: Float32Array, fit = 0.78): Float32Array {
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity]
  for (let i = 0; i < points.length; i += 3) {
    for (let k = 0; k < 3; k++) {
      lo[k] = Math.min(lo[k], points[i + k])
      hi[k] = Math.max(hi[k], points[i + k])
    }
  }
  const span = Math.max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])
  if (!(span > 0) || !Number.isFinite(span)) return points
  const scale = (2 * fit) / span
  for (let i = 0; i < points.length; i += 3) for (let k = 0; k < 3; k++) points[i + k] = (points[i + k] - (lo[k] + hi[k]) / 2) * scale
  return points
}

/** Grow a server-sampled point list (flat x,y,z) to n points: each repeat is nudged so the surface reads solid. */
export function fromPoints(flat: number[], n = SHAPE_N, seed = 13): Float32Array {
  const out = new Float32Array(n * 3)
  const count = Math.floor(flat.length / 3)
  if (!count) return out
  const r = rng(seed)
  for (let i = 0; i < n; i++) {
    const j = (i % count) * 3
    const jitter = i < count ? 0 : 0.006
    for (let k = 0; k < 3; k++) out[i * 3 + k] = (Number.isFinite(flat[j + k]) ? flat[j + k] : 0) + (r() - 0.5) * jitter
  }
  return normalize(out)
}

/** Points spread over the parts' surfaces in proportion to each part's area. */
export function fromParts(parts: Part[], n = SHAPE_N, seed = 7): Float32Array {
  const out = new Float32Array(n * 3)
  const usable = parts.filter((p) => p.size.every((v) => Number.isFinite(v) && v > 0) && p.at.every(Number.isFinite))
  if (!usable.length) return out
  const r = rng(seed)
  const weights = usable.map(area)
  const total = weights.reduce((a, b) => a + b, 0)
  let i = 0
  usable.forEach((part, k) => {
    const take = k === usable.length - 1 ? n - i : Math.round((weights[k] / total) * n)
    for (let j = 0; j < take && i < n; j++, i++) {
      const [x, y, z] = turn(onSurface(part, r), part.turn)
      out.set([x + part.at[0], y + part.at[1], z + part.at[2]], i * 3)
    }
  })
  return normalize(out)
}

export class ModelError extends Error {}

type Gltf = {
  scene?: number; scenes?: { nodes?: number[] }[]; nodes?: { mesh?: number; children?: number[]; matrix?: number[]; translation?: number[]; rotation?: number[]; scale?: number[] }[]
  meshes?: { primitives: { attributes: { POSITION?: number }; indices?: number; mode?: number }[] }[]
  accessors?: { bufferView?: number; byteOffset?: number; componentType: number; count: number; type: string }[]
  bufferViews?: { byteOffset?: number; byteLength: number; byteStride?: number }[]
  extensionsRequired?: string[]
}

type Mat = number[] // column-major 4x4, as glTF stores it

const IDENTITY: Mat = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]

function multiply(a: Mat, b: Mat): Mat {
  const out = new Array(16).fill(0)
  for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) for (let k = 0; k < 4; k++) out[c * 4 + r] += a[k * 4 + r] * b[c * 4 + k]
  return out
}

function local(node: NonNullable<Gltf['nodes']>[number]): Mat {
  if (node.matrix?.length === 16) return node.matrix
  const [tx, ty, tz] = node.translation ?? [0, 0, 0]
  const [qx, qy, qz, qw] = node.rotation ?? [0, 0, 0, 1]
  const [sx, sy, sz] = node.scale ?? [1, 1, 1]
  return [
    (1 - 2 * (qy * qy + qz * qz)) * sx, 2 * (qx * qy + qz * qw) * sx, 2 * (qx * qz - qy * qw) * sx, 0,
    2 * (qx * qy - qz * qw) * sy, (1 - 2 * (qx * qx + qz * qz)) * sy, 2 * (qy * qz + qx * qw) * sy, 0,
    2 * (qx * qz + qy * qw) * sz, 2 * (qy * qz - qx * qw) * sz, (1 - 2 * (qx * qx + qy * qy)) * sz, 0,
    tx, ty, tz, 1,
  ]
}

const COMPONENTS: Record<number, [number, (v: DataView, o: number) => number]> = {
  5121: [1, (v, o) => v.getUint8(o)], 5123: [2, (v, o) => v.getUint16(o, true)], 5125: [4, (v, o) => v.getUint32(o, true)],
  5126: [4, (v, o) => v.getFloat32(o, true)],
}

const MAX_TRIANGLES = 2_000_000

/** Points over the triangles of a binary glTF (.glb) model, with every node's transform applied. */
export function fromGlb(buffer: ArrayBuffer, n = SHAPE_N, seed = 11): { points: Float32Array; triangles: number } {
  const view = new DataView(buffer)
  if (buffer.byteLength < 20 || view.getUint32(0, true) !== 0x46546c67) throw new ModelError('This is not a .glb file.')
  if (view.getUint32(4, true) !== 2) throw new ModelError('Only glTF 2.0 models can be imported.')
  let json: Gltf | null = null
  let bin: DataView | null = null
  for (let at = 12; at + 8 <= Math.min(buffer.byteLength, view.getUint32(8, true));) {
    const length = view.getUint32(at, true), kind = view.getUint32(at + 4, true)
    if (at + 8 + length > buffer.byteLength) throw new ModelError('The model file is cut short.')
    if (kind === 0x4e4f534a) json = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, at + 8, length)))
    else if (kind === 0x004e4942) bin = new DataView(buffer, at + 8, length)
    at += 8 + length
  }
  if (!json || !bin) throw new ModelError('The model has no geometry inside.')
  const compressed = (json.extensionsRequired ?? []).find((e) => /draco|meshopt/i.test(e))
  if (compressed) throw new ModelError(`Compressed models (${compressed}) are not supported yet. Export without compression.`)

  const gltf = json
  const data = bin
  const read = (index: number, size: number): number[] => {
    const acc = gltf.accessors?.[index]
    const bv = acc?.bufferView === undefined ? undefined : gltf.bufferViews?.[acc.bufferView]
    const comp = acc && COMPONENTS[acc.componentType]
    if (!acc || !bv || !comp) throw new ModelError('The model uses a layout that cannot be read.')
    const [bytes, get] = comp
    const stride = bv.byteStride || bytes * size
    const base = (bv.byteOffset ?? 0) + (acc.byteOffset ?? 0)
    if (base + stride * (acc.count - 1) + bytes * size > data.byteLength) throw new ModelError('The model data is out of range.')
    const out = new Array(acc.count * size)
    for (let i = 0; i < acc.count; i++) for (let k = 0; k < size; k++) out[i * size + k] = get(data, base + i * stride + k * bytes)
    return out
  }

  const triangles: number[] = [] // 9 numbers each, already in world space
  const visit = (index: number, parent: Mat, depth: number) => {
    const node = gltf.nodes?.[index]
    if (!node || depth > 64) return
    const world = multiply(parent, local(node))
    for (const prim of node.mesh === undefined ? [] : gltf.meshes?.[node.mesh]?.primitives ?? []) {
      if ((prim.mode ?? 4) !== 4 || prim.attributes.POSITION === undefined) continue
      const pos = read(prim.attributes.POSITION, 3)
      const order = prim.indices === undefined ? Array.from({ length: pos.length / 3 }, (_, i) => i) : read(prim.indices, 1)
      for (let t = 0; t + 2 < order.length && triangles.length < MAX_TRIANGLES * 9; t += 3) {
        for (let k = 0; k < 3; k++) {
          const v = order[t + k] * 3
          if (v + 2 >= pos.length) throw new ModelError('The model points at a vertex that does not exist.')
          const [x, y, z] = [pos[v], pos[v + 1], pos[v + 2]]
          triangles.push(world[0] * x + world[4] * y + world[8] * z + world[12], world[1] * x + world[5] * y + world[9] * z + world[13],
            world[2] * x + world[6] * y + world[10] * z + world[14])
        }
      }
    }
    for (const child of node.children ?? []) visit(child, world, depth + 1)
  }
  const roots = gltf.scenes?.[gltf.scene ?? 0]?.nodes ?? (gltf.nodes ?? []).map((_, i) => i)
  for (const root of roots) visit(root, IDENTITY, 0)
  const count = triangles.length / 9
  if (!count || triangles.some((v) => !Number.isFinite(v))) throw new ModelError('The model has no triangles to build from.')

  const areas = new Float64Array(count)
  let total = 0
  for (let t = 0; t < count; t++) {
    const o = t * 9
    const ax = triangles[o + 3] - triangles[o], ay = triangles[o + 4] - triangles[o + 1], az = triangles[o + 5] - triangles[o + 2]
    const bx = triangles[o + 6] - triangles[o], by = triangles[o + 7] - triangles[o + 1], bz = triangles[o + 8] - triangles[o + 2]
    total += Math.hypot(ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx) / 2
    areas[t] = total
  }
  if (!(total > 0)) throw new ModelError('The model is flat or empty.')
  const r = rng(seed)
  const out = new Float32Array(n * 3)
  for (let i = 0; i < n; i++) {
    const pick = r() * total
    let lo = 0, hi = count - 1
    while (lo < hi) {
      const mid = (lo + hi) >> 1
      if (areas[mid] < pick) lo = mid + 1
      else hi = mid
    }
    let u = r(), v = r()
    if (u + v > 1) [u, v] = [1 - u, 1 - v]
    const o = lo * 9
    for (let k = 0; k < 3; k++) out[i * 3 + k] = triangles[o + k] + u * (triangles[o + 3 + k] - triangles[o + k]) + v * (triangles[o + 6 + k] - triangles[o + k])
  }
  return { points: normalize(out), triangles: count }
}

/** Wrap text into at most `maxLines` lines of about `width` characters, ending with an ellipsis if cut. */
export function wrap(text: string, width = 26, maxLines = 3): string[] {
  const words = text.replace(/\s+/g, ' ').trim().split(' ').filter(Boolean)
  const lines: string[] = []
  let line = ''
  let used = 0
  for (const word of words) {
    const piece = word.length > width ? word.slice(0, width - 1) + '…' : word
    if (line && (line + ' ' + piece).length > width) {
      lines.push(line)
      line = ''
      if (lines.length === maxLines) break
    }
    line = line ? line + ' ' + piece : piece
    used++
  }
  if (lines.length < maxLines && line) lines.push(line)
  if (used < words.length && lines.length) {
    const last = lines[lines.length - 1]
    lines[lines.length - 1] = (last.length + 1 > width ? last.slice(0, width - 1) : last) + '…'
  }
  return lines
}

/** Points that spell `lines` on a flat panel facing the viewer (browser only: it draws with a canvas). The font is
 * sized from the widest line so nothing is clipped; `fit` is how far the text may reach from the centre. */
export function fromText(lines: string[], n = SHAPE_N, seed = 5, fit = 0.85): Float32Array {
  const out = new Float32Array(n * 3)
  const canvas = document.createElement('canvas')
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  if (!ctx || !lines.length) return out
  const font = (px: number) => `700 ${px}px ui-monospace, SFMono-Regular, Menlo, monospace`
  ctx.font = font(100)
  const widest = Math.max(...lines.map((l) => ctx.measureText(l).width), 1)
  const px = Math.max(12, Math.min(96, Math.floor((100 * 960) / widest)))
  const lineHeight = Math.ceil(px * 1.25)
  canvas.width = 1024
  canvas.height = lineHeight * lines.length + 24
  ctx.font = font(px) // resizing the canvas reset the context
  ctx.fillStyle = '#fff'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  lines.forEach((line, i) => ctx.fillText(line, canvas.width / 2, 12 + lineHeight * (i + 0.5)))
  const { data, width, height } = ctx.getImageData(0, 0, canvas.width, canvas.height)
  const lit: number[] = []
  for (let i = 0; i < width * height; i++) if (data[i * 4 + 3] > 120) lit.push(i)
  if (!lit.length) return out
  const r = rng(seed)
  for (let i = 0; i < n; i++) {
    const at = lit[Math.floor(r() * lit.length)]
    out[i * 3] = (at % width) + r() - 0.5
    out[i * 3 + 1] = -(Math.floor(at / width) + r() - 0.5)
    out[i * 3 + 2] = (r() - 0.5) * 6
  }
  return normalize(out, fit)
}

/** Ask the swarm to build these points; `steady` holds them still and facing the viewer (for text). */
export function showShape(detail: ShapeDetail) {
  window.dispatchEvent(new CustomEvent<ShapeDetail | null>('vox:shape', { detail }))
}

/** Let the bots go back to the page's own formation. */
export function releaseShape() {
  window.dispatchEvent(new CustomEvent<ShapeDetail | null>('vox:shape', { detail: null }))
}
