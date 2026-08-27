# Working in this repo (guide for AI models)

This is a **presentation template**: a deck engine in the Tesco visual identity that
builds to **one self-contained HTML file**. Your job when asked to "make a deck" or
"add slides" is to author small HTML slide fragments and wire them into a manifest —
not to reinvent the engine.

Read [DESIGN.md](DESIGN.md) for the full design-system reference (tokens, components,
animation library, build model). This file is the **workflow and the rules**.

---

## Where a deck lives: local vs. template

**Default to building a _local deck_, not editing the template.** The slides under
`src/slides/examples/` are the template's **canonical examples** — a plain
`node build.js` always builds those, and you should treat them as read-only reference
unless the user explicitly asks to change the template itself.

A user's presentation is a self-contained folder under **`.local/slides/<name>/`**,
which is **git-ignored** — it stays on the user's machine and is never committed:

```
.local/slides/<name>/
  manifest.html   running order (one @build:inline per slide; bare filenames)
  <topic>.html    the slide files
  local.js        (optional) window.DECK_SECTIONS + bespoke scenes (Scenes.register)
  local.css       (optional) bespoke styles
```

Build a local deck with `node build.js --deck=<name>` → `dist/presentation-<name>.html`.
`local.js`/`local.css` are inlined **only** for that deck; a plain build has no local
layer. When the user says "make a deck about X", create `.local/slides/x/` and author
there — **do not** edit `src/slides.html` or the tracked engine files.

---

## Golden rules

1. **Never edit `dist/`.** It is generated. Edit sources (a local deck under `.local/`,
   or the template under `src/`), then run `node build.js`. Verify in the built file.
   **The template stays canonical** — put a user's deck in `.local/`, not in `src/`.
2. **Keep the brand.** Colours, font and logo are Tesco's and are fixed. Reference
   **tokens** (`var(--tesco-blue)`, `var(--surface)`, …) — never hard-code a hex, font
   or colour in a slide. All brand values live in `src/styles/tokens.css`; change them
   only there.
3. **Slides are generic content; the engine is not.** Put talk-specific words in slide
   files. Do not fork `deck.js`/`scenes.js` per talk — extend the shared library instead.
4. **One self-contained file is the deliverable.** No external CDNs, no frameworks, no
   build step beyond `node build.js`, no network at runtime. Anything visual must be
   inline SVG or a base64 data URI.
5. **Prefer composing the library over new CSS.** The components in `slides.css` cover
   most layouts. Add new CSS only when nothing fits, and make it theme-aware.

---

## How to add or change a slide

For a **local deck** (the default), work under `.local/slides/<name>/`; for the
**template's examples**, work under `src/slides/examples/` and `src/slides.html`. The
steps are the same either way:

1. **Create the file** — `.local/slides/<name>/<topic>.html` (or `src/slides/examples/`
   for the template). Name it by **topic, never by number**. Start from the closest
   example in `src/slides/examples/`.
2. **Follow the anatomy** (see DESIGN.md §4): an `@note` block, then a
   `<section class="slide" data-section="N" data-file="…">` with `.slide-inner` inside.
3. **Add speaker notes** in the `@note` block — plain text, `-` bullets fine. It must sit
   **directly above** the `<section>` or the build errors out. Never write a literal
   close-comment (`--` followed by `>`) inside a note.
4. **Register it in the manifest** — the deck's `manifest.html` (local) or `src/slides.html`
   (template). Add one `@build:inline` line in the position you want it to appear. Local
   manifest paths are **bare filenames** relative to the deck folder. The manifest is the
   *only* place order lives.
5. **Rebuild** — `node build.js --deck=<name>` (local) or `node build.js` (template) and
   confirm no warnings ("unreplaced tokens", "notes != slides").

To **reorder**, move the manifest line. To **remove**, delete the line. Nothing renumbers.

---

## Choosing a layout

| You want to… | Use |
|--------------|-----|
| Open the talk | `.title-wrap` hero (see `title.html`) |
| List agenda / pillars / features | `.card-grid` (`agenda.html`) |
| Make a sequence of points | `.bullets` (`bullets.html`) |
| Prose beside a stat / note | `.two-col` (`columns.html`) |
| Show a few key metrics (static) | `.stat-row > .stat` (`stat-cards.html`) |
| Label maturity / status / direction | `.tier`, `.trend`, `.pill` (`badges.html`) |
| Explain a system with a diagram | `.fig` + `.box`/`.lbl*` SVG helpers (`svg-diagram.html`) |
| Show headline metrics | `kpi` scene — counting numbers (`kpi.html`) |
| Compare categories | `bars` scene — bar chart (`bar-chart.html`) |
| Show a trend over time | `line` scene — line chart (`line-chart.html`) |
| Show percentages / scores | `rings` scene — radial dials (`rings.html`) |
| Show shares / completion | `progress` scene — horizontal bars (`progress.html`) |
| Show a roadmap / history | `timeline` scene (`timeline.html`) |
| Explain a "how it works" | `flow` scene — process steps (`process.html`) |
| Show data moving through a system | `discovery` scene — SVG graph (`scene-discovery.html`) |
| Map trust boundaries / threat model | `threat` scene — zone diagram (`scene-threat.html`) |
| Show parallel agents / concurrent checks | `hunt` scene — specialist swarm (`scene-hunt.html`) |
| Compare us vs them / tiers | `.matrix` table (`matrix.html`) |
| Quote a customer / mission | `.pullquote` (`quote.html`) |
| Drop a one-line hook | `.slide.punch` (`punch.html`) |
| Hand over to a new section | a divider slide with the next `data-section` (`divider.html`) |
| Close / sign off | recap `.card-grid` + logo (`closer.html`) |

Reveal static pieces one at a time with `data-frag` (group with `data-frag-group`); the
data-viz scenes animate themselves on arrival. See DESIGN.md §5–§7.

---

## Choosing an animation

Two mechanisms (DESIGN.md §7):

- **Fragments** (`data-frag`) — click-driven reveals. Default for building an argument.
- **Scenes** (`class="slide scene-slide" data-scene="…"`) — auto-play and **replay on
  every visit**. Seven are data-driven and need no JS:
  - `kpi` — big numbers that count up (`data-count`).
  - `bars` — a bar chart; columns grow to `data-val` (0–100).
  - `line` — a line chart from an inline `.line-data` JSON block.
  - `rings` — SVG donut dials; arc + centre number to `data-pct` / `data-count`.
  - `progress` — horizontal bars filling to `data-val` (0–100).
  - `timeline` — milestones lighting up in `data-step` order.
  - `flow` — process steps + arrows revealing in sequence.

For a custom scene, use a new `data-scene` name and lay out `.rise`/`.pop`/`.fadein`
elements with `--d` delays. If it needs timed JS, register a handler from the deck's
`local.js` with `window.Scenes.register("scene-name", fn)` — **don't** add a branch to
the tracked `scenes.js` for a single deck (only promote a scene into `scenes.js` if it's
genuinely reusable across decks). Don't put one-shot CSS animations on a non-scene slide
— they'll have already played before the audience arrives.

---

## Sections & timing

Slides map to sections via `data-section="N"`. A local deck declares its section names
and per-section time budgets by assigning `window.DECK_SECTIONS` (an array of
`{ name, budget }`, seconds) in its `local.js`; the template's own default lives in the
`SECTIONS` fallback at the top of `src/js/deck.js`. Either way, the count and order of
`data-section` values should line up with the array. The presenter console uses these
for the timing readout.

---

## Before you finish

- Run `node build.js`; there should be **no warnings**.
- Sanity-check the built deck in a browser: it must be served over `http://` for
  automated tools (the `file://` protocol is blocked in the sandbox), e.g.
  `cd dist && python3 -m http.server` then open the page. Presenter mode and `file://`
  double-click still work for the human — that path just isn't testable headlessly.
- Confirm: fonts load (Tesco Modern), titles are Tesco blue, the logo renders, fragments
  reveal in order, and any scene animates on navigation.
- Keep `DESIGN.md` in sync if you add a component or scene type.

---

## Anti-patterns (don't)

- ❌ Editing `dist/presentation.html` directly.
- ❌ Authoring a user's deck into `src/` — user decks go in `.local/slides/<name>/`.
- ❌ Editing tracked engine files (`slides.html`, `deck.js`, `scenes.js`, `slides.css`)
  for one deck — use `local.js`/`local.css` + `Scenes.register` / `DECK_SECTIONS`.
- ❌ Hard-coding `#003ADC` (or any hex/font) in a slide — use a token.
- ❌ Numbering slide filenames (`slide-03.html`) — name by topic.
- ❌ Adding a CDN link, web font, framework, or a second output file.
- ❌ Colouring a `?` red — the red full-stop is for a period at the end of a title only.
- ❌ Switching presenter sync to `BroadcastChannel` — it breaks over `file://`.
- ❌ One-shot CSS animations on a non-`scene-slide` slide (they fire before arrival).
