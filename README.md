# VoxAgent

Speak a request; the agent restates it, plans, runs the right tools, shows the result and says a short summary.
It reviews code, edits a real project with diffs you accept or reject, runs code in a sandbox, fixes its own
errors, and handles notes, reminders, email, calendar, GitHub and web forms.

One FastAPI backend holds all logic. The clients only record audio, show results and play speech:
the React PWA in `web/`, Telegram and WhatsApp bots, an Android wrapper and a desktop wrapper in `clients/`.

```
audio ─► speech-to-text ─► agent (plan, pick tools, permission gate) ─► tools ─► result ─► text-to-speech ─► client
                              │                                            │
                              ├─ memory + project NOTES.md + local time    ├─ workspace (git-versioned files)
                              └─ activity log, usage + cost per call       ├─ sandbox (docker | macOS seatbelt)
                                                                           └─ web, Google, GitHub, forms, deploy
```

## Quick start (local)

Needs Python 3.12+, [uv](https://docs.astral.sh/uv/), Node 20+, git, and a model: [Ollama](https://ollama.com)
locally, or any OpenAI-compatible API.

```bash
cd backend
cp .env.example .env              # set VOX_SECRET_KEY (openssl rand -hex 32); on a Mac without Docker set VOX_SANDBOX=seatbelt
uv sync
uv run playwright install chromium  # only for web form filling
uv run uvicorn app.main:app --port 8000
```

```bash
cd web
npm install
npm run dev                       # http://localhost:5173 (proxies the API)
```

Full stack with Postgres and the Docker sandbox: `docker compose up --build`, then open http://localhost:8080.
A guided walkthrough of every phase is in [DEMO.md](DEMO.md).

## What it can do

| Area | Tools the agent can call | Permission |
|---|---|---|
| Code review | `code_review` (ruff, mypy, secret scan, OSV dependency check + LLM; score, Markdown/PDF, share link), `github_post_review` | read · **yes** |
| Understanding | `explain` (beginner / intermediate / expert), `ask_project` (answers from the index and real files, cites path:line) | read |
| Workspace | `list_files`, `read_file`, `search_in_files`, `write_file`, `edit_file`, `multi_edit`, `rename_symbol`, `rename_file`, `delete_file`, `generate_code` (tests, docstrings, README), `edit_document` (.txt/.md/.docx) | stage only |
| Changes | `apply_changes` (say "apply"), `apply_deletions` (more than 5 deletes: say "yes"), `reject_changes`, `undo_change`, `change_history`, `open_workspace` | apply / yes |
| Running code | `run_command`, `install_packages` (checks the latest version first), `run_tests`, `start_server` (preview URL), `stop_server`, `read_logs`, `fix_and_run`, `create_project` | apply |
| Web | `web_search` (cites links), `web_fetch`, `package_version` | read |
| Seeing and hearing | `look_at_media` (up to 4 files at once: photos, screenshots, charts, receipts, PDFs including scanned pages; videos sampled into frames plus their soundtrack; audio transcribed; needs a vision model, ffmpeg and poppler-utils) | read |
| Maths | `calculate` (exact arithmetic, percentages, powers, roots, trig, logs, combinatorics, statistics), `solve_math` (equations and systems, factor/simplify/expand, derivatives, integrals, limits, series, matrices; every solution checked by substituting back; runs sandboxed with a time limit) | read |
| Personal | `remember`, `forget`, `list_memory`, `update_project_notes`, `save_note`, `search_notes`, `add_reminder`, `list_reminders`, `complete_reminder`, `daily_briefing` | read / apply |
| Accounts | `draft_email`, `send_email`, `create_event`, `github_open_pr`, `github_publish`, `deploy_project` | apply · **yes** |
| Forms | `fill_form` (preview, pauses), `submit_form`, `cancel_form` | apply · **yes** |
| Ordering | `order_from_link` (items, food and drinks, tools, subscriptions: walks to checkout, fills your details, stops at the final button with the total), `confirm_order`, `cancel_order` | apply · **yes** |
| Your Mac | `system_control`, `doc_writer`, `shopping` | apply · yes |

Ordering never types card numbers, CVV, passwords or OTPs; a checkout that needs them is handed back to you.
The final "place order / pay / subscribe" button is detected in code and never clicked by the browsing step,
and placing the order always happens on your next turn, after you have seen the preview and total.

The agent learns from corrections ("no, I meant…", "stop adding emojis") as standing preferences you can see and delete under memory. Answers cite their sources and say how sure they are.

Edit tools only stage a diff. Nothing reaches disk until you accept it (per change in the Workspace tab, or say
"apply"), and every accepted change is a git commit that "undo that" reverts. Sending, paying, submitting,
publishing, deploying and mass deletes always need an explicit yes.

## Environment variables

All settings are `VOX_*` variables read from `backend/.env`; `backend/.env.example` lists every one with a comment.
[docs/FREE-KEYS.md](docs/FREE-KEYS.md) walks through getting every key for free.
Each integration stays off until its variables are set:

| Feature | Set |
|---|---|
| Model | `VOX_LLM` (chain, first is primary), `VOX_LLM_BASE_URL`, `VOX_LLM_API_KEY`, `VOX_OLLAMA_URL` |
| Sandbox | `VOX_SANDBOX` = `docker` (build `sandbox/Dockerfile`) · `seatbelt` (macOS dev) · `off`; `VOX_TUNNEL=cloudflared` for public preview links |
| Google sign-in, Gmail, Calendar | `VOX_GOOGLE_CLIENT_ID`, `VOX_GOOGLE_CLIENT_SECRET` |
| GitHub | `VOX_GITHUB_CLIENT_ID` / `_SECRET` (or users paste a token) |
| Deploy | `VOX_DEPLOY_PROVIDER` = `render:<hook>` or `vercel` |
| Push notifications | `VOX_VAPID_PRIVATE_KEY` (`uv run python -m app.push keys`) |
| Bots | `VOX_TELEGRAM_TOKEN`; `VOX_WHATSAPP_TOKEN`, `_PHONE_ID`, `_VERIFY_TOKEN`, `_APP_SECRET` |
| Payments | `VOX_STRIPE_SECRET_KEY`, `VOX_STRIPE_PRICE_ID`, `VOX_STRIPE_WEBHOOK_SECRET` |

## Adding a skill

Create `backend/app/skills/<name>.py` exposing `skill` (one tool) or `skills` (a family). Each tool has:

- `name` and `description`: what the planner sees when choosing,
- `input_schema`: a Pydantic model for the arguments,
- `permission`: `READ` runs freely, `CHANGE` waits for "apply", `CRITICAL` waits for an explicit "yes",
- `async def run(self, args, ctx) -> SkillResult` (`ctx.emit` streams events, `ctx.complete` calls the model).

The registry discovers it at startup; the agent core does not change. Add `backend/tests/test_<name>.py`.
`app/skills/explain.py` is the smallest example.

## Checks

```bash
cd backend && uv run pytest -q && uv run ruff check . && uv run mypy app && uv run python -m evals.run
cd web && npm run build && npm run lint
```

CI (`.github/workflows/ci.yml`) runs the same on every push. Sandbox tests run on macOS and skip elsewhere.

## Deploy

- **Docker Compose** (`docker-compose.yml`): Postgres, the API (with the Docker sandbox) and the web app behind nginx.
  `VOX_WEB_PORT=8090 docker compose up --build` if 8080 is taken. Run it from the repo root: workspaces live in
  `./data`, mounted at the same path inside the API so the host's Docker can mount them into sandboxes. Preview
  servers share only the `voxagent_preview` network with the API, never Postgres. The API mounts the host Docker
  socket (root-equivalent); on a shared server use a dedicated sandbox host or microVM service instead.
- **Any container host**: build `backend/Dockerfile` and `web/Dockerfile`; set `VOX_DATABASE_URL` to Postgres
  (`postgresql+psycopg://…`, install with `uv sync --extra postgres`).
- **Clients**: install the PWA from the browser; `clients/android` (Trusted Web Activity via Bubblewrap) and
  `clients/desktop` (Electron, global push-to-talk) wrap the same deployed URL.

## Security model

- User code runs only in the sandbox. Seatbelt confines writes to the workspace and blocks reading the app,
  its database, other workspaces and `~/.ssh`, and connecting to localhost; Docker adds CPU, memory and process limits.
- Web fetches, form pages and their sub-requests refuse private, loopback, link-local and metadata addresses,
  re-checked on every redirect and pinned to the checked IP.
- Text from files, web pages, search results and repositories reaches the model wrapped as data, never as instructions.
- OAuth tokens, the GitHub token and the form profile are encrypted at rest (Fernet, key derived from `VOX_SECRET_KEY`).
  The form profile refuses passwords and card numbers.
- Webhooks (Stripe, WhatsApp) verify signatures; OAuth uses signed, expiring state.
- Users can export or delete everything, including workspaces, memory, notes and secrets.

## Known limits

- Rate limiter, WebSocket replay buffers and running preview servers live in process memory: run one worker
  (move them to Redis to scale out).
- Preview URLs proxy HTTP and WebSockets under `/preview/<id>/`; apps that hard-code absolute paths may need `VOX_TUNNEL=cloudflared`.
- Seatbelt is a development sandbox (no CPU or memory limits, macOS only). Use Docker in production.
- The planner sees every tool; small local models choose better with fewer skills installed.
- PDF review export uses the browser's print dialog.
- Not run against real accounts here: Gmail, Calendar, GitHub writes, Stripe, Telegram, WhatsApp, Render and
  Vercel deploys are tested against mocked APIs only. Ordering is tested end to end in Chromium against a local
  shop; real stores that need a login or card payment stop at that step. The Android build was not run (no
  Android SDK). The desktop app builds
  (`npm run dist`) and launches unsigned; signing needs an Apple Developer certificate.
