/**
 * The nanobot swarm: up to ~1M points drawn by WebGL2 with no vertex buffers. Every bot's position is derived on
 * the GPU from gl_VertexID, so a frame costs one draw call and a handful of uniforms; nothing is allocated per frame.
 *
 * Formations (brain, lattice, helix, ring) morph into each other; moods (idle, listen, think, speak) bend the
 * current formation. Bots assemble out of scattered dust when the swarm starts.
 *
 * Shapes (objects, imported models, text; see shapes.ts) live in two float textures, so the swarm can morph from a
 * formation or one shape into the next. A steady shape (text) stops turning and faces the viewer to stay readable.
 */
import { SHAPE_N, SHAPE_SIDE } from './shapes'

export type Mood = 'idle' | 'listen' | 'think' | 'speak'
/** Tell the swarm what the assistant is doing: listening, thinking, speaking or idle. */
export function setSwarmMood(mood: Mood) {
  window.dispatchEvent(new CustomEvent<Mood>('vox:swarm', { detail: mood }))
}

export const FORMATIONS = { brain: 0, lattice: 1, helix: 2, ring: 3 } as const
export type Formation = keyof typeof FORMATIONS

const VERTEX = `#version 300 es
precision highp float;
uniform float uTime, uCount, uAssemble, uMorph, uFromForm, uToForm, uDpr, uLight, uAspect, uSteady, uShift;
uniform highp sampler2D uShapeA, uShapeB;  // SHAPE_SIDE x SHAPE_SIDE points, xyz in rgb
uniform vec4 uMood;       // idle, listen, think, speak weights
uniform vec2 uPointer;    // -1..1
out vec3 vColor;
out float vAlpha;

// Integer hash: fract(sin(n)) needs sin() of ~1e7 radians, which Apple GPUs reduce accurately but D3D/ANGLE on
// Intel and AMD (most Windows laptops) do not, so the bots clumped into stripes there. Bits in, bits out: same everywhere.
float hash(float n) {
  uint x = floatBitsToUint(n);
  x ^= x >> 16; x *= 0x7feb352du; x ^= x >> 15; x *= 0x846ca68bu; x ^= x >> 16;
  return float(x >> 8) * (1.0 / 16777216.0);
}

vec3 formation(float form, float id, float t, float s1, float s2) {
  if (form > 3.5) {                          // a built shape: several bots per point, spread a little for volume
    int i = int(mod(id, float(${SHAPE_N})));
    ivec2 at = ivec2(i % ${SHAPE_SIDE}, i / ${SHAPE_SIDE});
    vec3 q = form < 4.5 ? texelFetch(uShapeA, at, 0).xyz : texelFetch(uShapeB, at, 0).xyz;
    return q + (vec3(s1, s2, hash(id + 1.7)) - 0.5) * 0.012;
  }
  if (form < 0.5) {                          // brain: two lobes of a dense neural cloud with filaments
    float phi = acos(1.0 - 2.0 * t);
    float theta = id * 2.399963;
    vec3 p = vec3(sin(phi) * cos(theta), cos(phi) * 0.82, sin(phi) * sin(theta));
    float r = s1 < 0.72 ? pow(s2, 0.42) : 1.0;
    p *= r * 0.62;
    p.x += sign(p.x) * 0.05;
    p += 0.035 * sin(p.yzx * 17.0 + s1 * 6.2831);
    return p;
  }
  if (form < 1.5) {                          // lattice: a tilted floor of bots in a grid
    float side = floor(sqrt(uCount));
    float gx = mod(id, side) / side - 0.5, gz = floor(id / side) / side - 0.5;
    return vec3(gx * 2.4, -0.38 + 0.03 * sin(gx * 18.0) * cos(gz * 18.0) + (s1 - 0.5) * 0.01, gz * 2.4);
  }
  if (form < 2.5) {                          // helix: a double strand with rungs
    float a = t * 6.2831 * 5.0;
    float strand = s1 < 0.5 ? 0.0 : 3.14159;
    float y = t * 1.6 - 0.8;
    float rung = step(0.86, s2);
    float rr = mix(0.32, 0.32 * (s1 * 2.0 - 1.0), rung);
    return vec3(cos(a + strand) * rr, y, sin(a + strand) * rr) + (vec3(s1, s2, hash(id + 7.0)) - 0.5) * 0.02;
  }
  float a = t * 6.2831;                      // ring: a thick orbital torus
  float b = s1 * 6.2831;
  float tube = 0.08 + 0.05 * s2;
  return vec3((0.62 + tube * cos(b)) * cos(a), tube * sin(b) * 1.4, (0.62 + tube * cos(b)) * sin(a));
}

void main() {
  float id = float(gl_VertexID);
  float t = (id + 0.5) / uCount;
  float s1 = hash(id), s2 = hash(id + 3.1), s3 = hash(id + 5.7);
  bool dust = s3 < 0.18;                     // a fifth of the bots drift as ambient dust across the whole screen

  vec3 p = mix(formation(uFromForm, id, t, s1, s2), formation(uToForm, id, t, s1, s2),
               smoothstep(0.0, 1.0, uMorph));
  if (dust) p = (vec3(s1, s2, hash(id + 9.3)) - 0.5) * vec3(5.0, 3.2, 3.0);

  float time = uTime;
  float calm = 1.0 - uSteady;                                           // steady shapes (text) hold still
  float swirl = time * (0.08 + uMood.z * 0.9) * (1.3 - length(p)) * calm;   // think: swirl, faster near the core
  p.xz = mat2(cos(swirl), -sin(swirl), sin(swirl), cos(swirl)) * p.xz;
  float bend = 1.0 - 0.85 * uSteady;
  p *= 1.0 + uMood.y * 0.08 * bend * sin(time * 5.0 + p.y * 9.0);           // listen: the cloud breathes
  p *= 1.0 + uMood.w * 0.12 * bend * sin(length(p) * 22.0 - time * 9.0);     // speak: waves run outward
  p += 0.006 * vec3(sin(time * 1.7 + s1 * 40.0), cos(time * 1.3 + s2 * 40.0), sin(time * 1.1 + s3 * 40.0));
  if (dust) p += vec3(sin(time * 0.05 + s2 * 6.0), cos(time * 0.04 + s1 * 6.0), 0.0) * 0.3;

  vec3 scatter = (vec3(s2, s3, s1) - 0.5) * 9.0;                       // assemble out of scattered dust
  p = mix(scatter, p, smoothstep(0.0, 1.0, clamp(uAssemble * 1.6 - s1 * 0.6, 0.0, 1.0)));

  float follow = 1.0 - 0.75 * uSteady;
  float ry = time * 0.06 * calm + uPointer.x * 0.35 * follow;         // slow turn, tilted toward the pointer
  float rx = 0.28 * calm + uPointer.y * 0.2 * follow;
  p.xz = mat2(cos(ry), -sin(ry), sin(ry), cos(ry)) * p.xz;
  p.yz = mat2(cos(rx), -sin(rx), sin(rx), cos(rx)) * p.yz;

  float z = p.z + 2.4;
  gl_Position = z < 0.2 ? vec4(2.0, 2.0, 2.0, 1.0) : vec4(p.x / z * 2.1 / uAspect, p.y / z * 2.1 + 0.22, 0.0, 1.0);  // lifted above the page's headline
  if (!dust) gl_Position.x += uShift;  // centred on the conversation area, not the window (the sidebar takes the left)
  gl_PointSize = clamp((dust ? 1.1 : 1.7) * uDpr * 2.4 / z, 1.0, 4.0 * uDpr);

  float energy = uMood.y + uMood.z + uMood.w;
  vec3 cyan = uLight > 0.5 ? vec3(0.02, 0.32, 0.45) : vec3(0.30, 0.92, 1.0);
  vec3 violet = uLight > 0.5 ? vec3(0.30, 0.14, 0.55) : vec3(0.62, 0.42, 1.0);
  vec3 spark = uLight > 0.5 ? vec3(0.55, 0.30, 0.02) : vec3(1.0, 0.78, 0.42);
  vColor = mix(mix(cyan, violet, clamp(p.y * 0.9 + s1 * 0.5, 0.0, 1.0)), spark, step(0.996 - energy * 0.01, s2));
  vAlpha = (dust ? 0.22 : 0.32 + energy * 0.25) * smoothstep(5.0, 1.5, z) * (uLight > 0.5 ? 1.4 : 1.0);
}`

const FRAGMENT = `#version 300 es
precision mediump float;
in vec3 vColor;
in float vAlpha;
out vec4 color;
void main() {
  float d = length(gl_PointCoord - 0.5);
  if (d > 0.5) discard;
  color = vec4(vColor, vAlpha * (1.0 - d * 2.0));
}`

const MOODS: Record<Mood, number> = { idle: 0, listen: 1, think: 2, speak: 3 }
const UNIFORMS = ['uTime', 'uCount', 'uAssemble', 'uMorph', 'uFromForm', 'uToForm', 'uDpr', 'uLight', 'uAspect', 'uMood', 'uPointer',
  'uSteady', 'uShapeA', 'uShapeB', 'uShift'] as const
const MORPH_MS = 1700
const SHAPE_A = 4
const SHAPE_B = 5
const MIN_BOTS = 1 << 15

function compile(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader {
  const shader = gl.createShader(type)!
  gl.shaderSource(shader, source)
  gl.compileShader(shader)
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader) ?? 'shader')
  return shader
}

/** How many bots this device should draw: about a million on a desktop GPU, fewer on phones. */
export function botBudget(width: number, cores: number, reducedMotion: boolean): number {
  if (reducedMotion) return MIN_BOTS
  if (width < 768 || cores <= 4) return 1 << 18
  return 1 << 20
}

export class Swarm {
  private gl: WebGL2RenderingContext
  private program: WebGLProgram
  private vao: WebGLVertexArrayObject
  private u = {} as Record<(typeof UNIFORMS)[number], WebGLUniformLocation | null>
  private raf = 0
  private born = performance.now()
  private mood = new Float32Array([1, 0, 0, 0])
  private target = new Float32Array([1, 0, 0, 0])
  private pointer = new Float32Array(2)
  private pointerTarget = new Float32Array(2)
  private from = 0
  private to = 0
  private morph = 1
  private morphStart = 0 // when the current morph began: morphs run on the clock, so a slow GPU never stalls one
  private steady = [0, 0, 0, 0, 0, 0] // per formation index: 1 holds the shape still and facing the viewer
  private textures: WebGLTexture[] = []
  private staging = new Float32Array(SHAPE_N * 4)
  private slow = 0
  private last = 0
  count: number
  light = false
  reducedMotion = false

  private canvas: HTMLCanvasElement

  constructor(canvas: HTMLCanvasElement, count: number) {
    this.canvas = canvas
    const gl = canvas.getContext('webgl2', { antialias: false, alpha: true, powerPreference: 'high-performance' })
    if (!gl) throw new Error('WebGL2 is not available')
    this.gl = gl
    // No GPU (hardware acceleration off, or Windows' "Basic Render Driver"): WebGL runs on the CPU, so draw the minimum.
    const info = gl.getExtension('WEBGL_debug_renderer_info')
    const renderer = String(gl.getParameter(info ? info.UNMASKED_RENDERER_WEBGL : gl.RENDERER))
    this.count = /swiftshader|llvmpipe|software|basic render/i.test(renderer) ? Math.min(count, MIN_BOTS) : count
    const program = gl.createProgram()!
    gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, VERTEX))
    gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, FRAGMENT))
    gl.linkProgram(program)
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program) ?? 'link')
    this.program = program
    this.vao = gl.createVertexArray()!
    for (const name of UNIFORMS) this.u[name] = gl.getUniformLocation(program, name)
    for (let i = 0; i < 2; i++) {
      const texture = gl.createTexture()!
      gl.bindTexture(gl.TEXTURE_2D, texture)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST)
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA32F, SHAPE_SIDE, SHAPE_SIDE, 0, gl.RGBA, gl.FLOAT, this.staging)
      this.textures.push(texture)
    }
    gl.useProgram(program)
    gl.uniform1i(this.u.uShapeA, 0)
    gl.uniform1i(this.u.uShapeB, 1)
  }

  setMood(mood: Mood) {
    this.target.fill(0)
    this.target[MOODS[mood]] = 1
  }

  setFormation(form: Formation) {
    const next = FORMATIONS[form]
    if (next === this.to) return
    this.from = this.morph < 1 ? this.from : this.to
    this.to = next
    this.morph = 0
    this.morphStart = performance.now()
  }

  /** Build a shape: SHAPE_N points as x, y, z. The bots morph from wherever they are into it. */
  /** Horizontal centre (-1..1 across the window) that steady shapes are drawn around. */
  shift = 0

  setShape(points: Float32Array, steady: boolean) {
    if (points.length < SHAPE_N * 3) return
    const slot = this.to === SHAPE_A ? SHAPE_B : SHAPE_A // never overwrite the shape the bots are leaving
    for (let i = 0; i < SHAPE_N; i++) {
      this.staging[i * 4] = points[i * 3]
      this.staging[i * 4 + 1] = points[i * 3 + 1]
      this.staging[i * 4 + 2] = points[i * 3 + 2]
    }
    const gl = this.gl
    gl.bindTexture(gl.TEXTURE_2D, this.textures[slot - SHAPE_A])
    gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, SHAPE_SIDE, SHAPE_SIDE, gl.RGBA, gl.FLOAT, this.staging)
    this.steady[slot] = steady ? 1 : 0
    this.from = this.to
    this.to = slot
    this.morph = 0
    this.morphStart = performance.now()
  }

  /** True while the bots hold (or are forming) a built shape rather than a page formation. */
  get shaped() {
    return this.to >= SHAPE_A
  }

  setPointer(x: number, y: number) {
    this.pointerTarget[0] = x
    this.pointerTarget[1] = y
  }

  private frame = (now: number) => {
    const gl = this.gl
    const dt = Math.min((now - (this.last || now)) / 1000, 0.1)
    this.last = now
    // Adaptive budget: when frames run long, halve the bots instead of dropping frames.
    if (dt > 0.028 && ++this.slow > 30 && this.count > MIN_BOTS) {
      this.count >>= 1
      this.slow = 0
      this.canvas.dataset.bots = String(this.count)
    }
    const ease = 1 - Math.exp(-dt * 3)
    for (let i = 0; i < 4; i++) this.mood[i] += (this.target[i] - this.mood[i]) * ease
    for (let i = 0; i < 2; i++) this.pointer[i] += (this.pointerTarget[i] - this.pointer[i]) * ease
    this.morph = Math.min(1, (now - this.morphStart) / MORPH_MS)

    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    const w = Math.floor(this.canvas.clientWidth * dpr)
    const h = Math.floor(this.canvas.clientHeight * dpr)
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w
      this.canvas.height = h
    }
    const seconds = (now - this.born) / 1000
    gl.viewport(0, 0, w, h)
    gl.clearColor(0, 0, 0, 0)
    gl.clear(gl.COLOR_BUFFER_BIT)
    gl.enable(gl.BLEND)
    gl.blendFunc(gl.SRC_ALPHA, this.light ? gl.ONE_MINUS_SRC_ALPHA : gl.ONE)
    gl.useProgram(this.program)
    gl.bindVertexArray(this.vao)
    const u = this.u
    gl.uniform1f(u.uTime, this.reducedMotion ? seconds * 0.15 : seconds)
    gl.uniform1f(u.uCount, this.count)
    gl.uniform1f(u.uAssemble, this.reducedMotion ? 1 : Math.min(1, seconds / 2.6))
    gl.uniform1f(u.uMorph, this.morph)
    gl.uniform1f(u.uFromForm, this.from)
    gl.uniform1f(u.uToForm, this.to)
    gl.uniform1f(u.uDpr, dpr)
    gl.uniform1f(u.uLight, this.light ? 1 : 0)
    gl.uniform1f(u.uAspect, w / Math.max(1, h))
    gl.uniform4fv(u.uMood, this.mood)
    gl.uniform2fv(u.uPointer, this.pointer)
    const m = this.morph * this.morph * (3 - 2 * this.morph)
    gl.uniform1f(u.uSteady, this.steady[this.from] * (1 - m) + this.steady[this.to] * m)
    gl.uniform1f(u.uShift, this.shift)
    for (let i = 0; i < 2; i++) {
      gl.activeTexture(gl.TEXTURE0 + i)
      gl.bindTexture(gl.TEXTURE_2D, this.textures[i])
    }
    gl.drawArrays(gl.POINTS, 0, this.count)
    this.raf = requestAnimationFrame(this.frame)
  }

  play() {
    if (!this.raf) this.raf = requestAnimationFrame(this.frame)
  }

  pause() {
    cancelAnimationFrame(this.raf)
    this.raf = 0
    this.last = 0
  }

  /** Free the GPU objects. The context itself stays alive: a remount (React StrictMode, hot reload) reuses the
   * same canvas, and a context we had forcibly lost would hand it back dead. */
  dispose() {
    this.pause()
    this.gl.deleteVertexArray(this.vao)
    for (const texture of this.textures) this.gl.deleteTexture(texture)
    this.gl.deleteProgram(this.program)
  }
}
