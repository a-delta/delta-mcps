# Design system

How this template looks, how it's put together, and how to extend it. Pair this
with [AGENTS.md](AGENTS.md) (rules of the road) — this file is the *reference*, that
one is the *workflow*.

The engine's job is to make **on-brand, animated, presenter-ready slides** from small
HTML fragments, then bake them into one file with no runtime dependencies.

---

## 1. Brand identity

The deck ships in **Tesco's visual identity**. All brand values live in
[`src/styles/tokens.css`](src/styles/tokens.css) — change them *there* and everything
downstream updates. Full palette + typography notes: [`assets/reference/tesco-brand.md`](assets/reference/tesco-brand.md).

### Colour

| Token | Value | Use |
|-------|-------|-----|
| `--tesco-blue` | `#003ADC` | Titles, chrome, primary accent. Brighter blue chosen for projector legibility. |
| `--tesco-red` | `#e81c2d` | The "full-stop" accent, emphasis, alert states. |
| `--tesco-light-grey` | `#f6f6f6` | Page background (light theme). |
| `--success` / `--warning` / `--info` | green / amber / blue | Status accents. |
| `--sev-critical…low` | purple→yellow | A severity scale for badges/bars. |
| `--accent-a` / `--accent-b` | `#39CD39` / `#D689FF` | Secondary/tertiary card stripes. |

**Never** hard-code a hex in a slide or component — reference a token. Themes remap the
semantic tokens (`--surface`, `--text`, `--border`, `--scene-*`), so any component built
on those inverts automatically.

### Typography

- **Tesco Modern** (self-hosted woff2, base64-embedded at build). Weights 300/400/500/700.
- `h1`/`h2` render in Tesco blue (a lighter blue on dark for legibility); `h3` in strong text.
- Sizes are fluid (`clamp(...)`) so slides scale from laptop to projector.

### Two house rules

- **The red full-stop.** End a title with `<span class="dot">.</span>` — a red period.
  Reserved for that only; **never** colour a `?` or other punctuation red.
- **The kicker eyebrow.** A small uppercase label above the title, prefixed by a round
  red dot (`<div class="kicker">…</div>`). Use it to name the section or slide type.

---

## 2. Themes

Two themes, toggled with `T` (persisted to `localStorage`). Set on `<html data-theme>`.

- **light** (default) — white surfaces on Tesco light-grey.
- **dark** — deep navy with a subtle branded radial gradient.

Each theme defines the same set of semantic tokens. Build components against
`--surface`, `--surface-2`, `--border`, `--text`, `--text-strong`, `--text-muted`,
`--shadow*`. For full-bleed animated scenes there's a parallel `--scene-*` set (tile
fill, tile border, subtle line, accent, veil) so a scene reads correctly in both themes.

---

## 3. Build model

`node build.js` assembles `src/index.html` into `dist/presentation.html`:

1. **`@build:inline <path>`** — a directive in a comment (`/* … */` or `<!-- … -->`)
   is replaced by the file's contents, recursively. This is how CSS, JS and every slide
   get inlined. Paths resolve relative to each file's own directory.
2. **`@note` extraction** — each slide carries speaker notes in an `<!-- @note … -->`
   block directly above its `<section>`. The build pulls them out **in document order**,
   keys them by slide position into a `NOTES` object for the presenter console, and
   **strips them from the audience deck**. A note not immediately above a `<section>` is
   a hard error (it would mis-key every subsequent note).
3. **Font tokens** — `__FONT_LIGHT__` … `__FONT_BOLD__` are replaced with base64 woff2.
4. **Logo token** — `__LOGO_SVG__` is replaced with the inlined `tesco-logo.svg`
   (a `viewBox` is injected if missing, so `height + width:auto` scales cleanly).

Result: one file, no external requests, works over `file://` — including presenter mode.

**Single-section build:** `node build.js --only=<section>` emits just the slides under
`src/slides/<section>/` → `dist/presentation-<section>.html`. Styles, JS and the manifest
are untouched; only slide files outside that folder are dropped.

---

## 4. Slide model

- **One file per slide** under `src/slides/<section>/<topic>.html`, named by **topic,
  never by number**.
- The running order lives **only** in the manifest [`src/slides.html`](src/slides.html)
  — one `@build:inline` line per slide. Reorder = move a line; add = new line + file;
  remove = delete the line. Nothing renumbers, so two branches only conflict if they
  touch the *same* slide (or the one line-list).
- Every slide is a `<section class="slide" data-section="N" data-file="…">`.
  `data-section` maps the slide to an entry in `SECTIONS` (name + time budget) in
  `deck.js`. `data-file` is documentation of where the fragment lives.

### Anatomy of a content slide

```html
<!-- @note
Speaker notes here. Plain text; line breaks and - bullets are fine.
Never write a literal close-comment sequence inside a note.
-->
<section class="slide" data-section="0" data-file="examples/agenda.html">
  <div class="slide-inner">
    <div class="kicker">Section label</div>
    <h2>Slide title<span class="dot">.</span></h2>
    <!-- content, using library components -->
  </div>
</section>
```

`.slide-inner` centres and width-caps content (`--maxw`). Add `class="slide grow"` when
content reveals downward (so revealing doesn't re-centre and shove earlier lines up).

---

## 5. Fragments (step reveals)

Any element with **`data-frag`** starts hidden and reveals on the next `→`. Give several
the **same `data-frag-group`** to reveal them together. `deck.js` counts the distinct
groups (ungrouped elements each count as their own step) and walks through them before
advancing to the next slide. `←` steps back.

```html
<li class="frag" data-frag>Revealed first</li>
<li class="frag" data-frag data-frag-group="pair">Revealed together…</li>
<li class="frag" data-frag data-frag-group="pair">…with this one</li>
```

The `.frag` class provides the fade-and-rise transition; `deck.js` toggles `.in`.

---

## 6. Component library

All in [`src/styles/slides.css`](src/styles/slides.css). Compose these; restyle via tokens.

**Static / layout components** (compose on any slide, reveal with `data-frag`):

| Component | Class | What it is |
|-----------|-------|-----------|
| Title / hero | `.title-wrap`, `.big-title`, `.subtitle` | Opening slide with logo. |
| Card grid | `.card-grid > .card` (`.alt`, `.feature`) | 1–4 cards with a coloured left stripe. `.cols-2` / `.cols-4` modifiers. |
| Bullets | `.bullets` (`.num` to auto-number) | Numbered list; reveal each `<li>` with `data-frag`. |
| Two-column | `.two-col` (`.tc-main`, `.tc-aside`) | Wide text column beside a narrow aside. |
| Punch | `.slide.punch`, `.punch-line/-response/-sub` | Headline-alone hook. |
| Pull quote | `.pullquote` (`.pq-mark`, `.pq-em`, `figcaption`) | Big attributed quotation. |
| Comparison matrix | `.matrix` (`.col-hot`, `.yes/.no/.partial`) | Feature table; reveal rows with `data-frag`. |
| Stat cards | `.stat-row > .stat` (`.good`, `.warn`, `.bad`) | Row of metric cards with big value, label, trend badge, and coloured top border. |
| Tier / trend / pill | `.tier-elite/high/medium/low`, `.trend-good/bad/stable`, `.pill` | Inline status chips. Drop them into any text, table cell, or card. |
| SVG diagram | `.fig` wrapping an inline `<svg>`; `.box`, `.box-hi`, `.lbl*` on SVG elements | Theme-aware inline diagram. Fills and text colours remap automatically in dark mode. |

**Data-visualisation scenes** (need `class="slide scene-slide" data-scene="…"` — see §7):

| Scene | `data-scene` | What it is |
|-------|-------------|-----------|
| KPI counters | `kpi` | `.kpi-grid > .kpi`; big numbers count up (`data-count`). |
| Bar chart | `bars` | `.bars > .bar[data-val]`; columns grow from the axis. |
| Line chart | `line` | `.line-chart` + a `.line-data` JSON block; trend draws on, dots pop. |
| Radial rings | `rings` | `.rings > .ring[data-pct]`; SVG dials sweep + count up. |
| Progress bars | `progress` | `.progress-list > .pl-row[data-val]`; horizontal fills. |
| Timeline | `timeline` | `.timeline > .tl-item[data-step]`; milestones light in order. |
| Process flow | `flow` | `.flow > .flow-step` / `.flow-arrow`; steps reveal in sequence. |
| Discovery graph | `discovery` | Animated SVG data-flow graph (sources → handlers → sinks). Edges draw in, nodes pop, a spark travels the highlighted path. See §7c. |
| Threat model | `threat` | Trust-zone boundary diagram. `.tz-zone` / `.tz-bound` / `.tz-threat` reveal with `.rise` / `.pop`. CSS-only. |
| Parallel swarm | `hunt` | Grid of `.spec-card` agents that scan and resolve to `clear`/`hit`. Code magnifier panel on the right. |

---

## 7. Animation library

Two mechanisms, often combined:

### a) Fragment reveals (click-driven)
`data-frag` — described above. Use for *building an argument* under presenter control.

### b) Scenes (auto-playing, self-restarting)
A slide marked `class="slide scene-slide"` with a **`data-scene`** attribute is a
*scene*: its entrance animations **replay every time you navigate to it**. Without this,
CSS animations fire once on page load — long before you reach the slide.
[`src/js/scenes.js`](src/js/scenes.js) calls `Scenes.replay()` on the active scene each
time the slide changes.

**Entrance helpers** (set a per-element delay with `style="--d:.3s"`):

| Class | Effect |
|-------|--------|
| `.rise` | fade + translate up |
| `.fadein` | fade only |
| `.pop` | fade + scale up |

Any of these inside a `scene-slide` restart automatically. To build a **bespoke** scene,
give it any `data-scene` name and lay out elements with `.rise`/`.pop`/`.fadein` + `--d`
delays — the generic replay restarts them for free.

**Ten data-driven scenes need no custom JS** (`scenes.js` reads the data attributes or
builds the visual from markup):

1. **`data-scene="kpi"`** — big numbers that count up. Each `.kpi` has a
   `.kpi-num[data-count="<target>"]` wrapping a `.val` span (plus optional `.prefix` /
   `.suffix`). The underline fills as it lands. See `slides/examples/kpi.html`.
2. **`data-scene="bars"`** — a bar chart. Each `.bar[data-val="0..100"]` grows its
   `.bar-col` to that height; the `.bar-val` label reveals above it. `.alt` / `.red`
   recolour a column. See `bar-chart.html`.
3. **`data-scene="line"`** — a line chart. Points live in an inline
   `<script type="application/json" class="line-data">` block (`points`, optional `min`/`max`);
   `scenes.js` builds the SVG axes/grid, draws the line on, fades the area, pops the dots.
   See `line-chart.html`.
4. **`data-scene="rings"`** — SVG donut dials. Each `.ring[data-pct]` sweeps its arc and
   the centre `.ring-num .val` counts up to `data-count` (defaults to the pct). `.good` /
   `.warn` / `.red` set the colour. See `rings.html`.
5. **`data-scene="progress"`** — horizontal progress bars. Each `.pl-row[data-val="0..100"]`
   fills its track to that width; the value reveals as it lands. `.alt` / `.good` / `.red`
   recolour a bar. See `progress.html`.
6. **`data-scene="timeline"`** — milestones that light in order. Each `.tl-item[data-step]`
   is revealed in sequence while the `.tl-rail` fills between them. Flips vertical on
   narrow screens. See `timeline.html`.
7. **`data-scene="flow"`** — a process flow. `.flow-step` cards and the `.flow-arrow`
   connectors reveal left to right; `.alt` / `.feature` recolour a step. See `process.html`.
8. **`data-scene="discovery"`** — an animated SVG data-flow graph. Edges draw in, nodes pop
   column by column, a spark travels the hot path. See §7c and `scene-discovery.html`.
9. **`data-scene="threat"`** — a trust-zone boundary diagram. `.tz-zone` / `.tz-bound` /
   `.tz-threat` all use `.rise` / `.pop` with `--d` delays. CSS-only. See `scene-threat.html`.
10. **`data-scene="hunt"`** — a parallel specialist swarm. Each `.spec-card[data-resolve]`
    starts scanning and resolves to `clear` or `hit` after `data-rd` seconds. A `.mag-panel`
    code magnifier animates on the right. See `scene-hunt.html`.

`.scan-tag` (a pulsing status line) and `.graph-head` (kicker + title + lead) are the
shared scene chrome. `.scene.graph-scene` is the dark full-bleed stage for the graph;
`.scene.stage-scene` is the same for the other scenes above.

### c) Discovery graph data format

The `discovery` scene reads an optional JSON object from the `<section>`'s `data-graph`
attribute. Omit it entirely to use the built-in e-commerce example.

```json
{
  "labels":  ["Where data comes in", "Our application", "Sensitive data"],
  "sources": [{ "id":"s1", "label":"Endpoint A", "icon":"🌐", "hot":true }],
  "mids":    [{ "id":"m1", "label":"Handler B", "hot":true }],
  "sinks":   [{ "id":"k1", "label":"Database C", "hot":true }],
  "edges":   [["s1","m1"], ["m1","k1"]],
  "hotPath": [["s1","m1"], ["m1","k1"]]
}
```

`hot:true` on a node and listing an edge in `hotPath` together paint the highlighted
(red) source-to-sink path. Nodes without `hot` use the column's default colour.
The SVG is rebuilt on every replay so edges re-draw from scratch each visit.

### d) Showcase slides (art-of-the-possible)

`src/slides/showcase/` holds three scenario slides that are **not reusable templates** —
they are fully interactive demonstrations of complex AI-driven workflows.

| File | Engine | What it shows |
|------|--------|---------------|
| `by-hand.html` | `src/js/byhand.js` | A SOC analyst working a security ticket step by step, one search at a time, with a live queue of incoming alerts. |
| `orchestrator.html` | `src/js/orch.js` | An AI orchestrator delegating to parallel sub-agents on a virtual run-clock; draggable scrub bar, 1×/2×/4× speed. |
| `parallel-tickets.html` | inline `<script>` | The single investigated ticket collapses into an 18-ticket SOC queue, each triaged by AI simultaneously. |

These slides depend on `byhand.js` and `orch.js` being inlined in the deck (already
wired in `src/index.html`). They are included in the template's canonical examples as
inspiration; for a real deck, build them into a local deck under `.local/slides/`.

---

## 8. Tools

### Laser pointer

Press **`L`** (or the 🔴 button in the bottom controls) to toggle a red glowing dot
that follows the mouse. The dot is `pointer-events:none` so clicks still reach the
slide. It appears only in the window where the mouse is active — so in presenter mode
the laser lives on the projector window, not the console.

`src/js/laser.js` exposes `Laser.{toggle, set, isOn}`. `deck.js` wires the button and
the `L` shortcut.

### Edit mode

Press the **Edit** button (bottom-left, injected by `src/js/editmode.js`) to make every
text element on the current slide `contenteditable`. Click any text and type to change
it. When done, press **Copy slide** — the active `<section>` is serialised, build tokens
(SVG logos) are restored, and the markup is copied to the clipboard with the path of
its source file (`slides/<part>/<topic>.html`) shown on the button.

Paste it over the source file, then run `node build.js` to rebuild.

Press **Esc** to exit edit mode without copying.

---

## 9. Chrome, timing & presenter mode

- **Topbar** — logo, tag line, and a **section pill** naming the current section.
- **Bottom** — slide counter, progress bar, and controls (theme / present / laser / help / prev / next).
- **Sections & timing** — `SECTIONS` in `deck.js` lists `{ name, budget }` per
  `data-section` index. The presenter console shows elapsed vs budget per section and a
  running total; the bar turns red when over.
- **Presenter mode** (`P`) — opens a second window (`#present`) as the projector view and
  turns the original into the **console** (notes, timing, next-slide preview). They sync
  over `window.opener` + `postMessage` so it works from `file://`.
  **Do not** switch this to `BroadcastChannel` — every `file://` page is a distinct null
  origin, so the channel never connects when the deck is opened from disk.

---

## 10. Extending the template

- **New component:** add its rules to `slides.css` using semantic + `--scene-*` tokens so
  it themes cleanly. Keep the markup a small, composable block.
- **New scene type:** either reuse a data-driven scene, or add a `data-scene` name and
  drive it with entrance helpers; for timed/logic-driven behaviour, add a branch in
  `scenes.js`'s `replay()` keyed on `scene.dataset.scene`.
- **Rebrand:** swap the font woff2 in `assets/fonts/`, the logo in `assets/brand/`, and
  the values in `tokens.css`. Nothing else should need to change.
