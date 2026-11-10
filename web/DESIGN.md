---
direction: Terminal instrument. Monospace labels, hairline rules, one amber accent, dense.
fonts: { label: "ui-monospace, SF Mono, Menlo, monospace", text: "system-ui, sans-serif" }
radius: { sm: 2px, md: 4px }
space: [4, 8, 12, 16, 24, 32, 48, 64]
---

# VoxAgent design tokens

A tool, not a brand page: calm density, real state on screen, no decoration.

| Role | Light | Dark | Use |
|---|---|---|---|
| surface | #f6f5f1 | #14161a | page |
| raised | #ffffff | #1c1f25 | panels, inputs |
| line | #d9d6cc | #2e323a | hairline borders |
| ink | #1a1c20 | #e9e7e1 | body text |
| muted | #5d6068 | #9a9da6 | labels, meta |
| accent | #a8660a | #e8a33d | the one action, live state |
| ok | #1f7a4d | #5fc08a | done, passed |
| warn | #9a6b00 | #e0b84c | medium severity |
| danger | #b3261e | #f07a6f | errors, critical |

Rules
- Type: 16px body at 1.6, 14px mono for labels and data. Weights 400 and 600 only.
- One primary action per screen (the mic). Everything else is a quiet bordered button.
- Borders are 1px hairlines. Radius is 2px or 4px. No shadows, gradients or glows.
- Severity is never colour alone: it always carries its word.
- Motion: 150ms opacity or transform only, off under prefers-reduced-motion.
- Touch targets are at least 44px. Focus ring is a 2px accent outline.
