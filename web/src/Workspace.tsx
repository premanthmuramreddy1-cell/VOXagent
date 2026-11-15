import { useCallback, useEffect, useRef, useState } from 'react'
import {
  FileCode2, FolderGit2, GitCommitHorizontal, GitBranch, Download, Upload, Plus, Save, Undo2, X, Check,
  TerminalSquare, Play, Square, ExternalLink, ListChecks,
} from 'lucide-react'
import { api, post, raw } from './api'
import { highlight } from './highlight'

type Space = { id: string; name: string }
type Change = { id: string; kind: string; path: string; new_path: string; diff: string }
type Version = { sha: string; when: string; message: string }
type Server = { id: string; command: string; port: number; url: string; running: boolean }
type Tab = { path: string; content: string; saved: string }
type Panel = 'changes' | 'history' | 'terminal'

function Diff({ text }: { text: string }) {
  return (
    <pre className="code-layer overflow-x-auto text-[12px] leading-5">
      {text.split('\n').map((line, i) => (
        <div
          key={i}
          className={line.startsWith('+') && !line.startsWith('+++') ? 'diff-add' : line.startsWith('-') && !line.startsWith('---') ? 'diff-del' : 'text-muted'}
        >
          {line || ' '}
        </div>
      ))}
    </pre>
  )
}

function Editor({ tab, onChange, onSave }: { tab: Tab; onChange: (v: string) => void; onSave: () => void }) {
  const pre = useRef<HTMLPreElement>(null)
  const lines = tab.content.split('\n').length
  return (
    <div className="relative flex min-h-0 flex-1 overflow-hidden">
      <div aria-hidden className="code-layer select-none border-r border-line bg-surface pr-3 text-right text-muted/60">
        {Array.from({ length: lines }, (_, i) => <div key={i}>{i + 1}</div>)}
      </div>
      <div className="relative min-w-0 flex-1 overflow-hidden">
        <pre
          ref={pre}
          aria-hidden
          className="code-layer pointer-events-none absolute inset-0 overflow-hidden text-ink"
          dangerouslySetInnerHTML={{ __html: highlight(tab.content, tab.path) + '\n' }}
        />
        <textarea
          aria-label={`Editing ${tab.path}`}
          spellCheck={false}
          className="code-layer absolute inset-0 h-full w-full resize-none overflow-auto bg-transparent text-transparent caret-accent outline-none"
          value={tab.content}
          onChange={(e) => onChange(e.target.value)}
          onScroll={(e) => {
            if (pre.current) {
              pre.current.scrollTop = e.currentTarget.scrollTop
              pre.current.scrollLeft = e.currentTarget.scrollLeft
            }
          }}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 's') {
              e.preventDefault()
              onSave()
            } else if (e.key === 'Tab') {
              e.preventDefault()
              const el = e.currentTarget
              const { selectionStart: s, selectionEnd: end } = el
              onChange(tab.content.slice(0, s) + '    ' + tab.content.slice(end))
              requestAnimationFrame(() => el.setSelectionRange(s + 4, s + 4))
            }
          }}
        />
      </div>
    </div>
  )
}

function Terminal({ ws, onFiles }: { ws: string; onFiles: () => void }) {
  const [command, setCommand] = useState('')
  const [output, setOutput] = useState('')
  const [busy, setBusy] = useState(false)
  const [servers, setServers] = useState<Server[]>([])
  const [port, setPort] = useState('3000')
  const box = useRef<HTMLPreElement>(null)

  const loadServers = useCallback(() => {
    api<Server[]>(`/workspaces/${ws}/servers`).then(setServers).catch(() => setServers([]))
  }, [ws])
  useEffect(loadServers, [loadServers])
  useEffect(() => {
    box.current?.scrollTo(0, box.current.scrollHeight)
  }, [output])

  async function run(kind: 'run' | 'tests') {
    setBusy(true)
    try {
      const response = await raw(`/workspaces/${ws}/${kind}`, {
        method: 'POST', body: JSON.stringify(kind === 'run' ? { command } : {}),
      })
      const reader = response.body!.getReader()
      const decoder = new TextDecoder()
      for (;;) {
        const { value, done } = await reader.read()
        if (done) break
        setOutput((o) => o + decoder.decode(value, { stream: true }))
      }
    } catch (e) {
      setOutput((o) => o + `\n[error] ${(e as Error).message}\n`)
    } finally {
      setBusy(false)
      onFiles()
    }
  }

  async function start() {
    try {
      await post(`/workspaces/${ws}/servers`, { command, port: Number(port) })
      setOutput((o) => o + `[started] ${command} on port ${port}\n`)
      loadServers()
    } catch (e) {
      setOutput((o) => o + `[error] ${(e as Error).message}\n`)
    }
  }

  return (
    <div className="flex h-full flex-col">
      <pre ref={box} className="code-layer min-h-0 flex-1 overflow-auto bg-surface text-[12px] text-ink" aria-live="polite">
        {output || 'Run a command inside this workspace\'s sandbox. Output streams here.'}
      </pre>
      <form
        className="flex flex-wrap items-center gap-2 border-t border-line p-2"
        onSubmit={(e) => {
          e.preventDefault()
          if (command.trim()) run('run')
        }}
      >
        <span className="font-mono text-xs text-accent">$</span>
        <input
          className="field min-w-40 flex-1 font-mono text-xs"
          placeholder="python main.py · npm install · go run ."
          aria-label="Command"
          value={command}
          onChange={(e) => setCommand(e.target.value)}
        />
        <button className="btn" disabled={busy || !command.trim()} type="submit"><Play className="h-3.5 w-3.5" /> Run</button>
        <button className="btn" disabled={busy} type="button" onClick={() => run('tests')}><ListChecks className="h-3.5 w-3.5" /> Tests</button>
        <input className="field w-20 font-mono text-xs" aria-label="Server port" value={port} onChange={(e) => setPort(e.target.value)} />
        <button className="btn" type="button" disabled={!command.trim()} onClick={start}>Start server</button>
      </form>
      {servers.length > 0 && (
        <ul className="border-t border-line p-2 font-mono text-xs">
          {servers.map((s) => (
            <li key={s.id} className="flex items-center gap-2 py-1">
              <span className={`h-1.5 w-1.5 rounded-full ${s.running ? 'bg-ok' : 'bg-muted'}`} />
              <span className="truncate text-muted">{s.command}</span>
              <a className="ml-auto inline-flex items-center gap-1 text-accent hover:underline" href={s.url} target="_blank" rel="noreferrer">
                preview <ExternalLink className="h-3 w-3" />
              </a>
              <button className="btn-ghost min-h-8" aria-label="Stop server" onClick={() => api(`/workspaces/${ws}/servers/${s.id}`, { method: 'DELETE' }).then(loadServers)}>
                <Square className="h-3 w-3" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default function WorkspaceView() {
  const [spaces, setSpaces] = useState<Space[]>([])
  const [ws, setWs] = useState('')
  const [files, setFiles] = useState<string[]>([])
  const [tabs, setTabs] = useState<Tab[]>([])
  const [active, setActive] = useState('')
  const [changes, setChanges] = useState<Change[]>([])
  const [versions, setVersions] = useState<Version[]>([])
  const [panel, setPanel] = useState<Panel>('changes')
  const [message, setMessage] = useState('')
  const zipInput = useRef<HTMLInputElement>(null)

  const say = (text: string) => setMessage(text)
  const fail = (e: unknown) => setMessage((e as Error).message)

  const refreshAll = useCallback(async (id: string) => {
    if (!id) return
    const [tree, pending, log] = await Promise.all([
      api<{ files: string[] }>(`/workspaces/${id}/files`),
      api<Change[]>(`/workspaces/${id}/changes`),
      api<Version[]>(`/workspaces/${id}/history`),
    ])
    setFiles(tree.files)
    setChanges(pending)
    setVersions(log)
  }, [])

  useEffect(() => {
    api<Space[]>('/workspaces').then((list) => {
      setSpaces(list)
      setWs(list[0]?.id ?? '')
    }).catch(fail)
  }, [])

  useEffect(() => {
    if (!ws) return
    let live = true
    Promise.resolve().then(() => { if (live) return refreshAll(ws) }).catch(fail)
    const timer = setInterval(() => refreshAll(ws).catch(() => {}), 4000)  // picks up changes the voice agent stages
    return () => {
      live = false
      clearInterval(timer)
    }
  }, [ws, refreshAll])

  async function openFile(path: string) {
    if (tabs.some((t) => t.path === path)) return setActive(path)
    try {
      const { content } = await api<{ content: string }>(`/workspaces/${ws}/file?path=${encodeURIComponent(path)}`)
      setTabs((t) => [...t, { path, content, saved: content }])
      setActive(path)
    } catch (e) {
      fail(e)
    }
  }

  async function save(path: string) {
    const tab = tabs.find((t) => t.path === path)
    if (!tab) return
    try {
      await api(`/workspaces/${ws}/file`, { method: 'PUT', body: JSON.stringify({ path, content: tab.content }) })
      setTabs((all) => all.map((t) => (t.path === path ? { ...t, saved: t.content } : t)))
      say(`Saved ${path}`)
      refreshAll(ws)
    } catch (e) {
      fail(e)
    }
  }

  async function decide(kind: 'apply' | 'reject', ids: string[]) {
    try {
      await post(`/workspaces/${ws}/changes/${kind}`, { ids })
      say(kind === 'apply' ? 'Changes applied and versioned.' : 'Changes discarded.')
      await refreshAll(ws)
      setTabs((all) => all.filter((t) => t.content !== t.saved))  // reload clean tabs from disk next open
    } catch (e) {
      fail(e)
    }
  }

  async function undo() {
    try {
      const { undone } = await post<{ undone: string }>(`/workspaces/${ws}/undo`)
      say(`Undid: ${undone}`)
      setTabs((all) => all.filter((t) => t.content !== t.saved))
      refreshAll(ws)
    } catch (e) {
      fail(e)
    }
  }

  async function newSpace() {
    const name = prompt('Name for the new workspace')
    if (!name) return
    try {
      const created = await post<Space>('/workspaces', { name })
      setSpaces((s) => [created, ...s])
      setWs(created.id)
      setTabs([])
    } catch (e) {
      fail(e)
    }
  }

  async function importZip(file: File) {
    const form = new FormData()
    form.append('file', file)
    try {
      const r = await api<{ files: number }>(`/workspaces/${ws}/import`, { method: 'POST', body: form })
      say(`Imported ${r.files} files.`)
      refreshAll(ws)
    } catch (e) {
      fail(e)
    }
  }

  async function importGithub() {
    const url = prompt('Public GitHub repository URL')
    if (!url) return
    say('Downloading repository…')
    try {
      const r = await post<{ files: number }>(`/workspaces/${ws}/import/github`, { url })
      say(`Imported ${r.files} files.`)
      refreshAll(ws)
    } catch (e) {
      fail(e)
    }
  }

  async function exportZip() {
    try {
      const blob = await (await raw(`/workspaces/${ws}/export`)).blob()
      const link = document.createElement('a')
      link.href = URL.createObjectURL(blob)
      link.download = `${spaces.find((s) => s.id === ws)?.name ?? 'workspace'}.zip`
      link.click()
      URL.revokeObjectURL(link.href)
    } catch (e) {
      fail(e)
    }
  }

  const tab = tabs.find((t) => t.path === active)
  const panels: [Panel, string, typeof ListChecks][] = [
    ['changes', `Changes${changes.length ? ` (${changes.length})` : ''}`, ListChecks],
    ['history', 'History', GitCommitHorizontal],
    ['terminal', 'Terminal', TerminalSquare],
  ]

  return (
    <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
      {/* Project tree */}
      <aside className="flex max-h-56 shrink-0 flex-col border-b border-line lg:max-h-none lg:w-64 lg:border-r lg:border-b-0">
        <div className="flex items-center gap-2 border-b border-line p-2">
          <FolderGit2 className="h-4 w-4 shrink-0 text-accent" />
          <select
            className="field min-h-8 flex-1 py-1 font-mono text-xs"
            aria-label="Workspace"
            value={ws}
            onChange={(e) => {
              setWs(e.target.value)
              setTabs([])
              post(`/workspaces/${e.target.value}/open`).catch(() => {})
            }}
          >
            {spaces.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          <button className="btn-ghost min-h-8 px-2" aria-label="New workspace" onClick={newSpace}><Plus className="h-4 w-4" /></button>
        </div>
        <ul className="min-h-0 flex-1 overflow-y-auto py-1 font-mono text-xs" aria-label="Files">
          {files.map((path) => (
            <li key={path}>
              <button
                className={`flex w-full items-center gap-1.5 truncate px-3 py-1 text-left hover:bg-raised ${path === active ? 'bg-raised text-ink' : 'text-muted'}`}
                style={{ paddingLeft: `${12 + (path.split('/').length - 1) * 12}px` }}
                onClick={() => openFile(path)}
              >
                <FileCode2 className="h-3 w-3 shrink-0" />
                <span className="truncate">{path.split('/').pop()}</span>
              </button>
            </li>
          ))}
        </ul>
        <div className="flex flex-wrap gap-1 border-t border-line p-2">
          <input ref={zipInput} type="file" accept=".zip" hidden onChange={(e) => e.target.files?.[0] && importZip(e.target.files[0])} />
          <button className="btn-ghost min-h-8 px-2" onClick={() => zipInput.current?.click()} title="Import zip"><Upload className="h-3.5 w-3.5" /> Zip</button>
          <button className="btn-ghost min-h-8 px-2" onClick={importGithub} title="Import from GitHub"><GitBranch className="h-3.5 w-3.5" /> GitHub</button>
          <button className="btn-ghost min-h-8 px-2" onClick={exportZip} title="Export as zip"><Download className="h-3.5 w-3.5" /> Export</button>
        </div>
      </aside>

      {/* Editor */}
      <section className="flex min-h-[320px] min-w-0 flex-1 flex-col">
        <div className="flex items-center overflow-x-auto border-b border-line" role="tablist">
          {tabs.map((t) => (
            <div key={t.path} className={`flex shrink-0 items-center border-r border-line ${t.path === active ? 'bg-raised' : ''}`}>
              <button role="tab" aria-selected={t.path === active} className="px-3 py-2 font-mono text-xs" onClick={() => setActive(t.path)}>
                {t.path.split('/').pop()}{t.content !== t.saved && <span className="ml-1 text-accent">●</span>}
              </button>
              <button
                className="pr-2 text-muted hover:text-ink"
                aria-label={`Close ${t.path}`}
                onClick={() => {
                  setTabs((all) => all.filter((x) => x.path !== t.path))
                  if (active === t.path) setActive('')
                }}
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          ))}
          {tab && (
            <button className="btn-ghost ml-auto min-h-8 shrink-0" disabled={tab.content === tab.saved} onClick={() => save(tab.path)}>
              <Save className="h-3.5 w-3.5" /> Save
            </button>
          )}
        </div>
        {tab ? (
          <Editor
            tab={tab}
            onChange={(content) => setTabs((all) => all.map((t) => (t.path === tab.path ? { ...t, content } : t)))}
            onSave={() => save(tab.path)}
          />
        ) : (
          <div className="flex flex-1 items-center justify-center p-6 text-center font-mono text-xs text-muted">
            Open a file from the tree, or ask the agent: "make a to-do app with login".
          </div>
        )}
        {message && <p className="border-t border-line px-3 py-1.5 font-mono text-[11px] text-muted" role="status">{message}</p>}
      </section>

      {/* Review, history, terminal */}
      <aside className="flex min-h-[280px] shrink-0 flex-col border-t border-line lg:w-[420px] lg:border-t-0 lg:border-l">
        <div className="flex border-b border-line" role="tablist">
          {panels.map(([key, label, Icon]) => (
            <button
              key={key}
              role="tab"
              aria-selected={panel === key}
              className={`flex flex-1 items-center justify-center gap-1.5 py-2.5 font-mono text-xs ${panel === key ? 'border-b-2 border-accent text-ink' : 'text-muted'}`}
              onClick={() => setPanel(key)}
            >
              <Icon className="h-3.5 w-3.5" /> {label}
            </button>
          ))}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {panel === 'changes' && (
            changes.length === 0 ? (
              <p className="p-4 font-mono text-xs text-muted">No staged changes. Edits the agent proposes appear here for review.</p>
            ) : (
              <div>
                <div className="flex gap-2 border-b border-line p-2">
                  <button className="btn btn-accent flex-1" onClick={() => decide('apply', [])}><Check className="h-3.5 w-3.5" /> Accept all</button>
                  <button className="btn flex-1" onClick={() => decide('reject', [])}><X className="h-3.5 w-3.5" /> Reject all</button>
                </div>
                {changes.map((c) => (
                  <article key={c.id} className="border-b border-line">
                    <header className="flex items-center gap-2 px-3 py-2">
                      <span className="label">{c.kind}</span>
                      <span className="truncate font-mono text-xs">{c.new_path ? `${c.path} → ${c.new_path}` : c.path}</span>
                      <button className="btn-ghost ml-auto min-h-8 px-2 text-ok" aria-label={`Accept change to ${c.path}`} onClick={() => decide('apply', [c.id])}><Check className="h-3.5 w-3.5" /></button>
                      <button className="btn-ghost min-h-8 px-2 text-danger" aria-label={`Reject change to ${c.path}`} onClick={() => decide('reject', [c.id])}><X className="h-3.5 w-3.5" /></button>
                    </header>
                    <Diff text={c.diff} />
                  </article>
                ))}
              </div>
            )
          )}
          {panel === 'history' && (
            <div>
              <div className="border-b border-line p-2">
                <button className="btn w-full" onClick={undo} disabled={versions.length < 2}><Undo2 className="h-3.5 w-3.5" /> Undo last change</button>
              </div>
              <ol className="font-mono text-xs">
                {versions.map((v) => (
                  <li key={v.sha} className="flex gap-3 border-b border-line px-3 py-2">
                    <span className="text-accent">{v.sha}</span>
                    <span className="min-w-0 flex-1 truncate">{v.message}</span>
                    <time className="shrink-0 text-muted" dateTime={v.when}>{new Date(v.when).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time>
                  </li>
                ))}
              </ol>
            </div>
          )}
          {panel === 'terminal' && ws && <Terminal ws={ws} onFiles={() => refreshAll(ws)} />}
        </div>
      </aside>
    </div>
  )
}
