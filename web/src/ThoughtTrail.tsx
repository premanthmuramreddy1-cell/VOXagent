import { CheckCircle2, ExternalLink, Loader2, XCircle } from 'lucide-react'

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
}

function toolLabel(skill: string): string {
  return LABELS[skill] ?? skill.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
}

const SAID = { running: 'in progress', ok: 'done', failed: 'failed' } as const

/** The agent's real steps for one answer, joined like a thought: each node lights up as its tool runs. */
export default function ThoughtTrail({ tools }: { tools: Tool[] }) {
  return (
    <ol className="thought-trail" aria-label="Steps taken">
      {tools.map((tool, index) => (
        <li key={index} className="thought-node" data-state={tool.state}>
          <span className="thought-dot" aria-hidden="true">
            {tool.state === 'running' && <Loader2 className="h-3 w-3 animate-spin" />}
            {tool.state === 'ok' && <CheckCircle2 className="h-3 w-3" />}
            {tool.state === 'failed' && <XCircle className="h-3 w-3" />}
          </span>
          <span className="font-semibold">{toolLabel(tool.skill)}</span>
          <span className="sr-only">{SAID[tool.state]}</span>
          {tool.error && <span className="text-danger">: {tool.error}</span>}
          {tool.data?.review_id && (
            <a className="inline-flex items-center gap-1 font-semibold underline hover:opacity-80"
               href={`#/reviews/${tool.data.review_id}`}>
              Report ({tool.data.score}/100)
              <ExternalLink className="h-2.5 w-2.5" />
            </a>
          )}
        </li>
      ))}
    </ol>
  )
}
