# Presentation template

A reusable deck engine in the **Prioclen Consulting (PCL) visual identity**, delivered
as a **single self-contained HTML file**. Brand — colours, the Aptos font, the PCL logo —
is baked in; the slides themselves are generic examples you replace with your own content.

> **The deliverable is `dist/presentation.html`.** Double-click it (or open in any
> browser) — no server, no internet, no other files needed. Fonts, logo, CSS and
> JavaScript are all embedded.

## Quick start

```bash
node build.js                    # -> dist/presentation.html  (the canonical examples deck)
node build.js --deck=<name>      # -> dist/presentation-<name>.html  (a local deck, see below)
```

Then open the built file. To change content, edit the sources and rebuild — never
hand-edit the file in `dist/`.

## Your own decks (local, not committed)

The template's committed slides are **canonical examples** — a plain `node build.js`
always builds those. Your own presentations live **outside the template**, under
`.local/slides/<name>/`, which is git-ignored: they never get committed, so the
template stays clean while you keep as many local decks as you like to reuse.

A local deck is a self-contained folder:

```
.local/slides/<name>/
  manifest.html     running order for this deck (one @build:inline per slide)
  <topic>.html      the slide files (named by topic)
  local.js          (optional) this deck's SECTIONS + any bespoke scenes
  local.css         (optional) this deck's bespoke styles
```

Build it with `node build.js --deck=<name>`. `local.css`/`local.js` are inlined only
for that deck; a plain build has no local layer at all. Set section names/budgets by
assigning `window.DECK_SECTIONS` in `local.js`; register a bespoke scene with
`window.Scenes.register("scene-name", fn)` there too — no need to touch `src/`.

## Presenting

- **Navigate:** `→` / `Space` (next step or slide), `←` (back).
- **Presenter mode:** press `P` (or the 🖥 button). This opens a **new window that is the
  presentation itself** — drag it to the projector and press `F` to fullscreen. Your
  original window becomes the **presenter console** (speaker notes, timing, next-slide
  preview). The audience window never shows notes. Sync uses `window.opener` +
  `postMessage`, which works over `file://` — no web server needed.
- **Theme:** `T` toggles light / dark (both official PCL themes). **Fullscreen:** `F`.
  **Help:** `?`.

## Editing

For a **local deck**, edit the files under `.local/slides/<name>/` (see above). For the
**template itself** (the shared engine and example slides):

- **A slide's words / speaker notes:** edit its file under `src/slides/examples/<topic>.html`.
- **Reorder / add / remove an example slide:** edit the manifest `src/slides.html` (one
  `@build:inline` line per slide, in running order). Nothing gets renumbered.
- **Default section names & budgets:** edit the `SECTIONS` fallback in `src/js/deck.js`
  (a local deck overrides these via `window.DECK_SECTIONS`).
- **Brand colours / theme:** edit `src/styles/tokens.css`.

## Source layout

```
src/
  index.html        HTML shell (chrome, help, presenter console, build placeholders)
  slides.html       MANIFEST — the deck's running order (one @build:inline per slide)
  slides/           the canonical example slides; named by topic, never by number
    examples/       layouts + animated scenes (a plain build ships these)
  styles/
    tokens.css      brand colours, fonts (@font-face), light + dark themes
    base.css        reset, typography, slide/fragment layout
    chrome.css      topbar, progress bar, controls, counter, help
    slides.css      component + animation library
    console.css     presenter console layout + mode visibility
  js/
    scenes.js       auto-replaying data-viz scenes (kpi, bars, line, rings, progress, timeline, flow)
    deck.js         navigation, fragments, theme, timing, two-window presenter sync
assets/
  fonts/            Aptos woff2, Latin-subset (base64-embedded at build)
  brand/            PCL logo SVGs (inlined at build) + the master artwork
  reference/        brand palette + typography notes
build.js            assembles everything into dist/ (Node only, no dependencies)
```

## Documentation

- **[DESIGN.md](DESIGN.md)** — the design system: tokens, layout components, the
  animation library, and how the build works.
- **[AGENTS.md](AGENTS.md)** — guidance for AI models building decks from this template.
  Referenced by **[CLAUDE.md](CLAUDE.md)**.
