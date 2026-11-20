import { useEffect, useState } from 'react'
import { AlertCircle, Check, ChevronRight, ExternalLink, Loader2 } from 'lucide-react'
import Sources from './Sources'

type Step = { text: string; skill: string; done: boolean }
type Tool = { skill: string; state: 'running' | 'ok' | 'failed'; data?: any; error?: string }

// Plain words for what each tool does. Only the tool's name is shown, never its arguments: they can hold private text.
const LABELS: Record<string, string> = {
  web_search: 'Searching the web', web_fetch: 'Reading the page', world_briefing: 'Gathering the news',
  code_review: 'Reviewing the code', explain: 'Reading your file', doc_writer: 'Writing the document',
  generate_code: 'Writing code', ask_project: 'Studying the project', fix_and_run: 'Fixing and re-running',
  create_project: 'Building the project', run_tests: 'Running the tests', run_command: 'Running a command',
  start_server: 'Starting the app', install_packages: 'Installing packages', read_file: 'Reading a file',
  write_file: 'Writing a file', edit_file: 'Editing a file', search_in_files: 'Searching the project',
  build_object: 'Building it in 3D', fill_form: 'Filling the form', submit_form: 'Submitting the form',
  groceries: 'Finding groceries', system_control: 'Using your computer',
  calculate: 'Calculating', solve_math: 'Solving the maths', look_at_media: 'Looking at it', recall: 'Searching your memory', deep_research: 'Researching in depth', agent_team: 'Working as a team',
}

function toolLabel(skill: string): string {
  return LABELS[skill] ?? skill.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
}

const SAID = { running: 'in progress', ok: 'done', failed: 'failed' } as const

type Phase = 'thinking' | 'working' | 'writing' | 'done' | 'issue' | 'rest'

/** The living mark: a soft core inside a turning ring. It breathes while thinking, spins while a tool runs, settles to a tick when done. */
export function Orb({ phase }: { phase: Phase }) {
  return (
    <span className="orb" data-phase={phase} aria-hidden="true">
      <span className="orb-ring" />
      <span className="orb-core" />
      {phase === 'done' && <Check className="orb-mark h-3 w-3" strokeWidth={3} />}
      {phase === 'issue' && <AlertCircle className="orb-mark h-3 w-3" strokeWidth={2.75} />}
    </span>
  )
}

const WORDS: Record<Phase, string> = {
  thinking: 'Thinking', working: 'Working', writing: 'Writing', done: 'Done', issue: 'Finished with a problem', rest: '',
}

type Props = {
  restate?: string
  understood?: string
  steps?: Step[]
  tools?: Tool[]
  live?: boolean
  answering?: boolean // the reply text has started streaming
  pending?: boolean // the turn is on its way but nothing has come back yet
}

/**
 * One calm status line for everything the agent does before and while it answers: what it understood, its plan,
 * the tools it runs and its reasoning. Collapsed to "Thinking…" / "Working…" with an animated orb; open it to see the detail.
 */
export default function Thinking({ restate, understood, steps, tools, live, answering, pending }: Props) {
  const [choice, setChoice] = useState<boolean | null>(null) // null: follow the turn (open while live, shut after)
  const [seconds, setSeconds] = useState(0)

  const running = tools?.find((t) => t.state === 'running')
  const failed = tools?.some((t) => t.state === 'failed') ?? false
  const phase: Phase = live || pending
    ? running ? 'working' : answering ? 'writing' : 'thinking'
    : failed ? 'issue' : 'done'

  const thinking = (tools ?? []).flatMap((t) => (t.data?.thinking as string[] | undefined) ?? [])
  const decision = tools?.find((t) => t.data?.comparison)?.data?.comparison as string | undefined
  const hasDetail = Boolean(restate || (steps && steps.length) || (tools && tools.length) || thinking.length)

  const active = Boolean(live || pending)
  const [started] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const timer = setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000)
    return () => clearInterval(timer)
  }, [active, started])

  if (!active && !hasDetail) return null

  const open = hasDetail && (choice ?? active)
  const doneCount = steps?.filter((s) => s.done).length ?? 0
  const summary = active
    ? running ? toolLabel(running.skill) : phase === 'writing' ? 'Putting the answer together' : restate ?? 'Understanding your request'
    : steps && steps.length > 1 ? `${doneCount} of ${steps.length} steps` : tools?.length ? `${tools.length} ${tools.length === 1 ? 'step' : 'steps'}` : ''

  const body = (
    <div className="think-body">
      <div className="think-inner">
        <ol className="think-timeline">
          {restate && (
            <li className="think-row" data-state="ok" style={{ animationDelay: '0ms' }}>
              <span className="think-label">Goal</span>
              <span className="think-text">{restate}</span>
              {understood && <span className="think-sub">Read as “{understood}”</span>}
            </li>
          )}
          {steps?.map((step, index) => (
            <li key={`s${index}`} className="think-row" data-state={step.done ? 'ok' : active ? 'idle' : 'skipped'}
                style={{ animationDelay: `${(index + 1) * 60}ms` }}>
              <span className="think-label">Step {index + 1}</span>
              <span className={step.done ? 'think-text think-done' : 'think-text'}>{step.text}</span>
            </li>
          ))}
          {tools?.map((tool, index) => (
            <li key={`t${index}`} className="think-row" data-state={tool.state}
                style={{ animationDelay: `${((steps?.length ?? 0) + index + 1) * 60}ms` }}>
              <span className="think-label">
                {tool.state === 'running' && <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />}
                {tool.state === 'ok' && <Check className="h-3 w-3" aria-hidden="true" />}
                {tool.state === 'failed' && <AlertCircle className="h-3 w-3" aria-hidden="true" />}
                Tool
              </span>
              <span className="think-text">{toolLabel(tool.skill)}</span>
              <span className="sr-only">{SAID[tool.state]}</span>
              {tool.error && <span className="think-sub text-danger">{tool.error}</span>}
              {tool.data?.review_id && (
                <a className="think-sub inline-flex items-center gap-1 font-semibold underline hover:opacity-80"
                   href={`#/reviews/${tool.data.review_id}`}>
                  Report ({tool.data.score}/100) <ExternalLink className="h-2.5 w-2.5" />
                </a>
              )}
            </li>
          ))}
          {thinking.map((line, index) => (
            <li key={`r${index}`} className="think-row" data-state="ok">
              <span className="think-label">Reasoning</span>
              <span className="think-text">{line}</span>
            </li>
          ))}
          {decision && (
            <li className="think-row" data-state="ok">
              <span className="think-label">Decided</span>
              <span className="think-text italic">{decision}</span>
            </li>
          )}
        </ol>
        {tools && tools.length > 0 && <Sources tools={tools} />}
      </div>
    </div>
  )

  return (
    <section className="think" data-phase={phase} data-open={open} aria-label="Agent activity">
      <button type="button" className="think-head" aria-expanded={open} disabled={!hasDetail}
              onClick={() => setChoice(!open)}>
        <Orb phase={phase} />
        <span className="think-title">
          <span className={active ? 'shimmer' : undefined}>{WORDS[phase]}</span>
          {active && <span className="think-dots" aria-hidden="true"><i /><i /><i /></span>}
        </span>
        {summary && <span className="think-summary">{summary}</span>}
        {active && seconds > 0 && <span className="think-time">{seconds}s</span>}
        {hasDetail && <ChevronRight className="think-chevron h-3.5 w-3.5" aria-hidden="true" />}
      </button>
      {hasDetail && body}
    </section>
  )
}
