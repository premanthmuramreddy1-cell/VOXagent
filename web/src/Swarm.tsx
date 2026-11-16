import { useEffect, useRef } from 'react'
import { botBudget, Swarm as Engine, type Formation, type Mood } from './swarmEngine'
import type { ShapeDetail } from './shapes'

/**
 * The living backdrop: a full-screen field of nanobots behind every page. Decorative, so it is hidden from
 * assistive tech; the app works the same when WebGL2 is missing.
 */
/** Where the page's content column sits, as -1..1 across the window (0 when there is no marked stage). */
function stageCentre() {
  const stage = document.querySelector('[data-swarm-stage]')?.getBoundingClientRect()
  return stage && stage.width ? ((stage.left + stage.width / 2) / innerWidth) * 2 - 1 : 0
}

export default function Swarm({ formation, dark }: { formation: Formation; dark: boolean }) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const engine = useRef<Engine | null>(null)
  const page = useRef(formation)

  useEffect(() => {
    const el = canvas.current
    if (!el) return
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches
    let swarm: Engine
    try {
      swarm = new Engine(el, botBudget(innerWidth, navigator.hardwareConcurrency || 4, reduced))
    } catch {
      return // no WebGL2: the CSS backdrop alone still looks right
    }
    swarm.reducedMotion = reduced
    engine.current = swarm
    el.dataset.bots = String(swarm.count)
    swarm.play()

    const recentre = () => (swarm.shift = stageCentre())
    const onMood = (e: Event) => {
      recentre()
      swarm.setMood((e as CustomEvent<Mood>).detail)
    }
    const onShape = (e: Event) => {
      const shape = (e as CustomEvent<ShapeDetail | null>).detail
      recentre()
      document.documentElement.toggleAttribute('data-botshow', Boolean(shape)) // the UI steps back while bots perform
      if (shape) swarm.setShape(shape.points, shape.steady)
      else if (swarm.shaped) swarm.setFormation(page.current) // back to the page's own formation
      el.dataset.shape = shape?.label ?? ''
    }
    const onMove = (e: PointerEvent) => swarm.setPointer((e.clientX / innerWidth) * 2 - 1, (e.clientY / innerHeight) * 2 - 1)
    const onVisibility = () => (document.hidden ? swarm.pause() : swarm.play())
    const onLost = (e: Event) => {
      e.preventDefault() // keep the page alive; the backdrop just stops
      swarm.pause()
    }
    addEventListener('vox:swarm', onMood)
    addEventListener('vox:shape', onShape)
    addEventListener('resize', recentre)
    const settle = setTimeout(recentre, 300) // the stage is laid out after the first paint
    addEventListener('pointermove', onMove, { passive: true })
    document.addEventListener('visibilitychange', onVisibility)
    el.addEventListener('webglcontextlost', onLost)
    return () => {
      removeEventListener('vox:swarm', onMood)
      removeEventListener('vox:shape', onShape)
      removeEventListener('resize', recentre)
      clearTimeout(settle)
      removeEventListener('pointermove', onMove)
      document.removeEventListener('visibilitychange', onVisibility)
      el.removeEventListener('webglcontextlost', onLost)
      swarm.dispose()
      engine.current = null
    }
  }, [])

  useEffect(() => {
    page.current = formation
    if (engine.current) engine.current.shift = 0 // other pages have no stage: centre again until one appears
    engine.current?.setFormation(formation)
    const id = setTimeout(() => engine.current && (engine.current.shift = stageCentre()), 300)
    return () => clearTimeout(id)
  }, [formation])

  useEffect(() => {
    if (engine.current) engine.current.light = !dark
  }, [dark])

  return (
    <div aria-hidden="true" className="swarm-backdrop pointer-events-none fixed inset-0 z-0">
      <canvas ref={canvas} className="h-full w-full" />
    </div>
  )
}
