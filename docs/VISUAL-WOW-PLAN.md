# VoxAgent: the "wait, how did it do that?" plan

**Direction, in one line:** *The swarm is VoxAgent's body.* Every feature below is the same 65,536 points (one per texel of a 256×256 texture, `SHAPE_N` in `web/src/shapes.ts`) showing a different truth: your voice, the agent's thinking, your day, your memory, a thing you asked to see. No second visual language, no stock art, no gradient blobs.

**Rules that hold for all 10**
- **Free.** No paid service. One new npm dependency at most (`qrcode`, for #8 and #10), added only with your yes.
- **Honest.** Visuals only show real state, such as real tool steps, real reminders and real memories. Nothing animates to look busy.
- **60 fps or nothing.** The frame budget is ≤16.6 ms. The swarm already scales to the device (`botBudget()` in `swarmEngine.ts:145`), and new effects are shader uniforms, not new draw passes.
- **Reduced motion and screen readers come first.** Every effect has a `prefers-reduced-motion` still state and a plain-text twin in the DOM (`aria-live` for the spoken parts).
- **Built on what exists.** The swarm already has moods (`idle / listen / think / speak`), formations, `setShape()`, a `vox:shape` event, GLB and text to points, and a backend event stream (`tool.started`, `plan.step`, `answer.delta`, `swarm.build`…). Most features are new *uses* of these, not new engines.

---

## Shared foundation, built once (F0)

| Piece | What | Where |
|---|---|---|
| **F0.1 Voice level** | An `AnalyserNode` on both `Mic` (your voice) and `Player` (its voice). Every frame it sends `{level 0–1, pitch 0–1}` as a `vox:level` event. Pitch is the spectral centroid of the FFT, normalised. | `web/src/audio.ts` |
| **F0.2 Swarm uniforms** | Four new shader inputs: `uLevel`, `uPitch`, `uFocus` (a vec2 point the bots lean toward), `uExplode`. Each is eased on the CPU side so nothing jumps. | `web/src/swarmEngine.ts` |
| **F0.3 Event fan-out** | `Chat.tsx` already switches on server events. It also re-dispatches them as `vox:*` window events (the same pattern as `vox:shape`), so new components listen without touching `Chat.tsx` again. | `web/src/Chat.tsx` |
| **F0.4 Part ids** | The shape texture's unused alpha channel holds a *part index*, so any shape knows which points belong to the "wheel" and which to the "door". | `web/src/shapes.ts` |

accept: `npm run build` → exit 0 · `node scripts/shapes-check.ts` → passes, with a new check that part ids survive `fromParts()` and `fromGlb()`

---

## The 10 features

### 1. The living orb ★ Phase 1
**You see:** while you talk, the swarm breathes with your voice: louder means wider, higher pitch means a cooler colour. While it thinks, the points fall into a tight, slowly turning core. While it answers, the surface ripples with *its* syllables, because the analyser listens to the TTS output, not a fake timer.
**How:** F0.1 + F0.2. The `speak` mood multiplies its displacement by `uLevel`, and hue shifts by `uPitch` within the accent token's range. `setSwarmMood()` (`Chat.tsx:597`) stays the single place that sets the mood.
**Files:** `audio.ts`, `swarmEngine.ts`, `Chat.tsx`. **Backend:** none.
**Reduced motion:** no breathing; a single soft glow ring scales with the level.
**accept:** `npm run build` → 0 · screen recording at 375/1440 shows the level following the voice · performance trace: frame ≤16.6 ms on a mid laptop.

### 2. Watch it think ★ Phase 1
**You see:** for "check tomorrow's weather and remind me if it rains", three glowing nodes appear (*search → read → remind*). The swarm streams from node to node as each tool runs; a finished node settles and a failed one flickers red. You see *real* work, not a spinner.
**How:** the backend already emits `tool.started`, `tool.finished` and `plan.step` (`agent.py:408–427`). New `ThoughtTrail.tsx` draws the nodes as an SVG overlay, and `uFocus` pulls the bots toward the active node.
**Backend:** `tool.started` gains `label`, a short human name such as "Searching the web". It's never the raw arguments, because they can hold private text.
**Files:** `agent.py`, new `web/src/ThoughtTrail.tsx`, `swarmEngine.ts`.
**accept:** `uv run pytest -q tests/test_agent.py` → passes, with a new test that `tool.started` carries `label` and no argument values · a manual multi-step task shows one node per real tool call.

### 3. Say it, see it in 3D ★ Phase 2
**You see:** "show me a jet engine" and the swarm becomes one. Drag to spin it. Pinch, or press the *Explode* button, and the parts drift apart along their own axes. Tap a part and its name appears; say "what's this?" and VoxAgent explains *that* part.
**How:** `build_tools.py` already sends parts or GLB points through `swarm.build`, and `botShow.ts` builds them. New: F0.4 part ids, `uExplode` (each point moves out along its part's centroid direction), pointer drag that sets rotation (`setPointer()` exists), and tap hit-testing on the CPU against part centroids.
**Backend:** `swarm.build` sends `part_names[]`. A new `explain_part` path reuses the `explain` skill with the part name and object.
**Files:** `shapes.ts`, `swarmEngine.ts`, `Swarm.tsx`, `botShow.ts`, `build_tools.py`.
**A11y:** a parts list in the DOM; each item focuses its part.
**accept:** `node scripts/shapes-check.ts` → explode keeps every point finite · `uv run pytest -q tests/test_build*.py` → passes · a 5-part model shows 5 tappable parts.

### 4. Your day as a sky (Phase 3)
**You see:** a new **Sky** view.
- Reminders are stars, brighter the sooner they're due.
- Bills are small planets whose orbits tighten as the due date nears.
- Habits are constellations whose lines light up on streak days.

Tap any of them for its card: done, snooze, pay-link or log.
**How:** read-only from existing endpoints: `/reminders`, `/habits` (`backend/app/api/personal.py`) and the bills API. New pure function `skyLayout(data, now) → points + hit boxes`, so it can be tested without a GPU. The points go to the swarm through `setShape()`; the hit boxes become real `<button>`s, so keyboard and screen readers work.
**Files:** new `web/src/Sky.tsx`, `web/src/skyLayout.ts`, a route in `App.tsx`. **Backend:** none.
**accept:** new `node scripts/sky-check.ts` → same input gives the same layout, a bill due today sits on the tightest orbit, an empty day shows "clear sky" text.

### 5. Mood-reactive theme (Phase 2)
**You see:** calm indigo at night, warm amber at dawn, neutral by day, set from your local time. When a turn is about something urgent (a bill due today, a missed medicine), a thin red edge appears and fades once it's handled.
**How:** a second token map per time band in `index.css` (`data-daypart="dawn|day|dusk|night"`), never a filter. The planner already returns JSON (`PLAN_SCHEMA` in `agent.py`), so it gains `tone: "calm" | "urgent"`, sent with `agent.restate`.
**Files:** `index.css`, `Chat.tsx`, `agent.py`.
**accept:** contrast ≥4.5:1 for body text in all 4 dayparts × light and dark, checked by a script over the tokens · a pytest that a schema-breaking `tone` falls back to `calm`.

### 6. Voice duet ★ Phase 1
**You see:** your words appear as you speak (`transcript.partial`) in a soft ink, letters settling from a slight blur into place. Its reply writes itself in a second ink, in time with the voice. It's like watching a conversation being handwritten.
**How:** new `Duet.tsx` keeps a settled text node and animates only the newest letters (transform and opacity only, the newest 40 at most) so long answers stay cheap. Markdown answers still go through `markdown.ts` once settled.
**A11y:** the animated spans are `aria-hidden`; one plain-text twin is read by `aria-live="polite"` on `answer.done`.
**Files:** new `web/src/Duet.tsx`, `Chat.tsx`, `index.css`.
**accept:** `npm run build` → 0 · `node scripts/markdown-check.ts` → passes · a 2,000-character answer keeps frames ≤16.6 ms.

### 7. Memory map (Phase 3)
**You see:** "what do you know about me?" opens a glowing web of what it remembers: people, places, projects and likes, joined where they share words. Pinch a node, or press Forget, and it dissolves into the swarm. The memory is really deleted, with an *Undo* that lasts 10 s.
**How:** `GET /memory` and `DELETE /memory/{id}` already exist (`personal.py:86,109`). Links come from shared keywords in a pure function (no LLM, no cost). The layout is a small force simulation in `graphLayout.ts`, about 80 lines with no dependency.
**Security:** delete stays scoped to the user who owns the memory. Undo re-posts the saved text through the existing `POST /memory`.
**Files:** new `web/src/MemoryMap.tsx`, `graphLayout.ts`. **Backend:** none, plus one deny test.
**accept:** `uv run pytest -q tests/test_personal*.py` → passes, with a new test that deleting another user's memory returns 404 · `node scripts/graph-check.ts` → the layout settles and no node is NaN.

### 8. Build reel (Phase 2)
**You see:** "make a site for my tuition class" plays like a film. Files fly in as the swarm writes them, a failing test flashes red then *heals* when the fix loop repairs it, and the live site unfolds as a card with a QR code. Scan it and it's on your phone.
**How:** the events already exist: `changes.applied`, `terminal.output`, `plan.step` and `server.started` (`run_tools.py`, `workspace.py`). New `BuildReel.tsx` turns them into a timeline. The QR code needs `qrcode` (your yes needed); without it the card shows the link and a copy button.
**Files:** new `web/src/BuildReel.tsx`, `Workspace.tsx`.
**accept:** a recorded event fixture plays back in `scripts/reel-check.ts` with the steps in order and a red step followed by a heal step · one real build reaches the card.

### 9. Spatial sound ★ Phase 1
**You see (hear):** its voice comes from where the orb sits on screen. Reminders chime softly from the left and alerts from the right. On headphones it feels like it's in the room.
**How:** `Player` routes through a `StereoPannerNode` (works on all browsers; `PannerNode` HRTF where supported). The pan follows the orb's screen x. Chimes are synthesised with oscillator envelopes, so there are no audio files.
**Respect:** the existing mute and volume settings; off when the system prefers reduced motion *and* the user turned off sounds.
**Files:** `audio.ts`, new `web/src/chimes.ts`.
**accept:** `npm run build` → 0 · a manual headphone check, left and right, written down in the report.

### 10. Postcards (Phase 3)
**You see:** a *Share* button on the good moments: a finished plan, a 30-day habit streak, a site it built, a 3D model. It makes a beautiful card with your swarm snapshot, the real fact ("Meditated 30 days in a row") and a small VoxAgent mark, sent straight to WhatsApp or Instagram.
**How:** draw to a 2D canvas from the WebGL frame (`preserveDrawingBuffer` only during capture) and get a PNG. Shared with the Web Share API (`navigator.share({files})`), with a download as the fallback. Optional 3 s animated WebM via `MediaRecorder`, where the browser supports it.
**Honesty:** a card can only show numbers that came from the API: no invented streaks, no fake stats.
**Files:** new `web/src/postcard.ts`, `Postcard.tsx`.
**accept:** `node scripts/postcard-check.ts` → card text comes only from the passed data · on a phone, Share opens the system sheet.

---

## Build order

```
Phase 1  "every conversation feels alive"   F0 → #1 orb → #6 duet → #2 thought trail → #9 spatial sound
Phase 2  "show a friend"                    #5 mood theme → #3 3D explode → #8 build reel
Phase 3  "your life, made visible"          #4 sky → #7 memory map → #10 postcards
```
Why this order: Phase 1 changes **every** turn and needs nothing new from the backend except one label. Phase 2 is the demo moment. Phase 3 adds new views that sit on top of Phase 1's polish.

## How it gets built
- One branch, `feat/visual-wow`; one commit per feature, each only after its `accept:` passes.
- Lanes run in parallel: **UI** (shaders and components), **CODE** (the small backend changes and tests) and **CONNECT** (`Chat.tsx` event fan-out). Each lane's work is reviewed by a different lane before merge.
- Every feature also passes the full gate: `uv run pytest -q`, `uv run ruff check app tests`, `npx tsc --noEmit`, `npm run build`, `npm run lint`, screenshots at 375/768/1440 in light and dark, console clean, and a performance trace ≤16.6 ms per frame.

## Risks, named now
- **Weak GPUs and phones:** the uniforms are cheap, but #3 explode and #4 sky push the swarm. `botBudget()` and the software-renderer cap stay in charge, with the 2D fallback when WebGL is missing.
- **Safari:** `MediaRecorder` WebM (#10) and HRTF (#9) vary by browser, so PNG and stereo pan are the guaranteed paths.
- **Privacy:** #2 labels and #10 cards must never show tool arguments or private text. Each has a test that enforces it.
- **Scope:** this is about 15–20 files. If time runs short, Phase 1 alone is the version that sets VoxAgent apart.
