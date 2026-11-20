---
direction: Nanoswarm. A deep-space instrument whose every surface is assembled from bots. Translucent glass panels with a dot-matrix texture float over a live WebGL swarm; one bioluminescent cyan accent; mono labels.
fonts: { label: "JetBrains Mono", text: "Plus Jakarta Sans" }
radius: { md: 12px, lg: 16px }
space: [4, 8, 12, 16, 24, 32, 48, 64]
---

# VoxAgent design tokens

The swarm (`src/swarmEngine.ts`, mounted by `src/Swarm.tsx`) is the product's body: up to ~1M nanobots
drawn by the GPU. It forms a brain on Talk, a lattice on Workspace, a helix on Notes and a ring on Reviews and
Account, and it mirrors the conversation: it breathes while listening, swirls while thinking and ripples while
speaking. The UI is glass over it, never an opaque slab.

| Role | Light | Dark | Use |
|---|---|---|---|
| void | #eef3fa | #03050b | page behind the swarm |
| surface | white-blue 72% | navy 60% | app chrome (glass) |
| raised | white 80% | navy 66% | panels, inputs, cards (glass) |
| line | navy 16% | cyan 16% | hairline borders |
| dot | teal 10% | cyan 7% | the nanobot dot-matrix on glass |
| ink | #0b1424 | #e8f1ff | body text |
| muted | #4b5b75 | #93a6c6 | labels, meta |
| accent / on-accent | #0a7ea4 / white | #5ee7ff / #02121a | the one action, live state |
| ok / warn / danger | green / amber / rose | same, brighter | status, always with a word |

Rules
- Glass surfaces use `bg-surface`/`bg-raised` plus `glass` (blur and dot matrix). Never an opaque full-page layer.
- Glow and violet live only inside the swarm; the UI stays flat glass and hairlines. One exception: the Thinking orb (`src/Thinking.tsx`) and the soft halo of a card while the agent is busy.
- Agent activity is one component, never stacked boxes: an orb that breathes while thinking, spins while a tool runs and settles to a tick when done. Goal, plan, tools and reasoning fold open beneath it.
- Body text 16px at 1.6; labels 11 to 12px mono. Weights 400 and 600.
- One primary action per screen (the mic). Touch targets at least 44px. Focus ring is a 2px accent outline.
- Motion: the swarm runs on the GPU with an adaptive bot budget; it slows to a near-still 32k bots under
  prefers-reduced-motion, pauses when the tab is hidden, and the page works without WebGL2.
