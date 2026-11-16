# VoxAgent demo scripts

One script per phase. Start the backend and web app as in the README (on a Mac without Docker, set
`VOX_SANDBOX=seatbelt`), sign up, then follow along. Say the lines with the mic button, or type them.

## Phase 1 — voice, planning, accounts, code review

1. Sign up with email (or Google, if `VOX_GOOGLE_CLIENT_ID` is set).
2. Say: **"What can you do?"** — a plain answer, no tools, spoken back.
3. Say: **"Explain this for a beginner: for i in range(3): print(i)"** — the `explain` tool runs.
4. Reviews tab → paste a file with a hard-coded password → you get a score, issues with file, line and fix,
   then Export (Markdown), Print / PDF and Share link.
5. Start talking while it answers: speech stops at once (barge-in).
6. Account → Download my data, and see usage under Account.

## Phase 2 — workspace, editor, index, undo

1. Workspace tab → a project with `NOTES.md` exists.
2. Say: **"Create a file called greet.py with a function getUser that returns 'Asha'"** — a diff appears in
   chat and under Changes. Click ✓ on it (or say **"apply"**).
3. Say: **"Rename getUser to fetchUser everywhere"** — one diff per file. Accept one, reject another.
4. Say: **"Undo that"** — History shows the revert as a new version.
5. Say: **"Where is fetchUser defined?"** — the answer cites `greet.py:1` from the real file.
6. Import a zip or a public GitHub repo; Export downloads the project as a zip.

## Phase 3 — sandbox, fix loop, previews, web

1. Workspace → Terminal: run `python3 -c "print(6*7)"` — output streams, exit code shows.
2. Write `main.py` containing `print(1/0)`. Say: **"Run main.py and fix it"** — `fix_and_run` reads the error,
   searches the web, edits, reruns; each fix is an undoable version.
3. Say: **"Make a small website that says hello and run it"** — `create_project` writes files and starts it;
   the chat shows a Preview link. With `VOX_TUNNEL=cloudflared` the link is public (trycloudflare.com).
4. Say: **"What is the latest version of fastapi?"** and **"Search the web for python asyncio timeout"** — answers cite links.
5. Try: **"Open http://127.0.0.1:8000/healthz"** — refused as an internal address.

## Phase 4 — GitHub, deploy, memory, notes, reminders, email, calendar

1. Say: **"Remember that I prefer Python and 4-space indents"** → Notes tab → What I remember (edit or delete it).
2. Say: **"Note that the demo is on Friday #work"** → it appears with tags; search "demo".
3. Say: **"Remind me tomorrow at 9 to call the bank"** → listed under Reminders in your time zone.
   Click **Turn on notifications** (needs `VOX_VAPID_PRIVATE_KEY`) to get reminders and the morning briefing as push.
4. Say: **"Give me my daily briefing"**.
5. Account → Connections → Google: then **"Email asha@example.com that the build is green"** — a draft shows;
   **"send it"** asks for an explicit yes. **"Add a meeting tomorrow 3 to 4 pm"** creates a calendar event.
6. Account → GitHub (token): **"Open a pull request on owner/repo with these changes on branch voxagent/demo"**
   (asks for yes). **"Post the last review on <PR URL>"**. With `VOX_DEPLOY_PROVIDER` set: **"Deploy it"**.

## Phase 5 — forms, bots, clients, payments

1. Notes → Form-filling profile: add name, email, city. Try adding `password` — it is refused.
2. Say: **"Fill the form at <public form URL>"** — a screenshot preview shows what was filled; it stops.
   **"Submit it"** needs an explicit yes.
3. Say: **"Order 2 iced lattes with oat milk from <menu link>"** (or a product, tool or subscription page): the chat
   shows each step, a screenshot and the total, and stops before "Place order". Next turn, **"yes, place it"**.
   A checkout that wants a card number, password or OTP is handed back to you.
4. Telegram (with `VOX_TELEGRAM_TOKEN`): Account → Telegram → Get code, send `/link CODE` to the bot,
   then type or send a voice note. Confirmations arrive as Yes/No buttons.
5. WhatsApp (Cloud API webhook at `/webhooks/whatsapp`): send `link CODE`, then talk to it the same way.
6. Desktop: `cd clients/desktop && npm install && npm start`, press **Cmd/Ctrl+Shift+Space** from any app.
7. Android: follow `clients/android/README.md`, or install the PWA from Chrome.
8. Payments (with Stripe keys): Account → Upgrade → Stripe Checkout; the webhook flips the plan to Paid and
   daily limits disappear.
