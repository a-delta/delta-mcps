#!/usr/bin/env node
/* ============================================================
   BUILD — assemble the self-contained presentation.

   Inlines every CSS/JS source (@build:inline), base64-embeds the
   Aptos fonts, and inlines the PCL logo SVGs. The output
   has NO external dependencies and works over file:// — including
   presenter mode, which opens a second copy of this same file
   (#present) and syncs via window.opener + postMessage.

   Usage:  node build.js                 # canonical examples deck
           node build.js --only=examples # only slides/<section> subdir
           node build.js --deck=<name>   # a local deck in .local/slides/<name>/
   Output: dist/presentation.html
           (or dist/presentation-<section|deck>.html)
   ============================================================ */
const fs = require("fs");
const path = require("path");

// ---- CLI ----
const ARGV = process.argv.slice(2);
function flagArg(name) {
  const eq = ARGV.find(a => a.startsWith(`--${name}=`));
  if (eq) return eq.split("=")[1] || null;
  const i = ARGV.indexOf(`--${name}`);         // space form: --name value
  if (i !== -1 && ARGV[i + 1]) return ARGV[i + 1];
  return null;
}
const ONLY = flagArg("only");
const DECK = flagArg("deck");                  // local deck under .local/slides/

const ROOT = __dirname;
const SRC = path.join(ROOT, "src");
const ASSETS = path.join(ROOT, "assets");
const DIST = path.join(ROOT, "dist");
// A local deck lives entirely under .local/ (git-ignored): its manifest,
// slides, and optional local.js / local.css. Plain builds ignore it.
const DECK_ROOT = DECK ? path.join(ROOT, ".local", "slides", DECK) : null;
if (DECK && !fs.existsSync(DECK_ROOT)) { console.error(`✗ No local deck at ${path.relative(ROOT, DECK_ROOT)}`); process.exit(1); }
const OUT = path.join(DIST, DECK ? `presentation-${DECK}.html` : ONLY ? `presentation-${ONLY}.html` : "presentation.html");

const read = p => fs.readFileSync(p, "utf8");
const b64 = p => fs.readFileSync(p).toString("base64");

// ---- logo (inline SVG) ----
function loadSvg(file) {
  const p = path.join(ASSETS, "brand", file);
  if (!fs.existsSync(p)) { console.warn("  ! missing SVG:", file); return ""; }
  // strip XML prolog if present; keep the <svg> element
  let svg = read(p).replace(/<\?xml[^>]*\?>\s*/i, "").trim();
  // Ensure a viewBox exists so height + width:auto scales reliably at any size.
  if (!/viewBox=/.test(svg)) {
    const w = (svg.match(/\bwidth="([\d.]+)"/) || [])[1];
    const h = (svg.match(/\bheight="([\d.]+)"/) || [])[1];
    if (w && h) svg = svg.replace(/<svg\b/, `<svg viewBox="0 0 ${w} ${h}"`);
  }
  return svg;
}
const LOGO_SVG = loadSvg("pcl-logo.svg");        // full lockup — mark + wordmark
const LOGO_MARK_SVG = loadSvg("pcl-mark.svg");   // mark only — for small chrome slots

// ---- fonts (base64) ----
const FONTS = {
  __FONT_LIGHT__:       "Aptos-Light-web.woff2",
  __FONT_REGULAR__:     "Aptos-Regular-web.woff2",
  __FONT_SEMIBOLD__:    "Aptos-SemiBold-web.woff2",
  __FONT_BOLD__:        "Aptos-Bold-web.woff2",
  __FONT_BLACK__:       "Aptos-Black-web.woff2",
  __FONT_ITALIC__:      "Aptos-Italic-web.woff2",
  __FONT_BOLD_ITALIC__: "Aptos-BoldItalic-web.woff2",
};

// Aptos Display is the brand's header face but is not redistributed with Office
// on every platform. If the woff2 files are dropped into assets/fonts/ we emit
// the @font-face rules for them; otherwise --font-display falls back to Aptos.
const DISPLAY_FONTS = [
  { weight: 400, file: "AptosDisplay-Regular-web.woff2" },
  { weight: 700, file: "AptosDisplay-Bold-web.woff2" },
];
function displayFontFaces() {
  const rules = DISPLAY_FONTS
    .filter(f => fs.existsSync(path.join(ASSETS, "fonts", f.file)))
    .map(f => `@font-face{font-family:"Aptos Display";font-weight:${f.weight};font-style:normal;`
            + `font-display:swap;src:url(data:font/woff2;base64,`
            + `${b64(path.join(ASSETS, "fonts", f.file))}) format("woff2");}`);
  if (!rules.length) console.log("  · Aptos Display not present — headers fall back to Aptos");
  return rules.join("\n");
}

// ---- @build:inline resolver ----
// Replaces  /* @build:inline path */  or  <!-- @build:inline path -->
function resolveInlines(html, baseDir, depth = 0) {
  if (depth > 10) throw new Error("inline recursion too deep");
  const re = /(?:\/\*|<!--)\s*@build:inline\s+([^\s*>]+)\s*(?:\*\/|-->)/g;
  return html.replace(re, (_, rel) => {
    // ---- local-deck hooks (only meaningful with --deck) ----
    if (rel === "slides.html" && DECK_ROOT) {
      // swap the canonical manifest for the local deck's manifest; its slide
      // paths resolve relative to the deck folder.
      const m = path.join(DECK_ROOT, "manifest.html");
      return resolveInlines(read(m), DECK_ROOT, depth + 1);
    }
    if (rel === "styles/local.css" || rel === "js/local.js") {
      // deck-specific style/JS: pulled from the deck folder if present, else
      // dropped entirely (a plain build has no local layer).
      if (!DECK_ROOT) return "";
      const p = path.join(DECK_ROOT, path.basename(rel));
      return fs.existsSync(p) ? read(p) : "";
    }
    // --only: drop slide files outside the requested section
    // (a slide path is slides/<section>/<file>.html; the manifest + styles/js are untouched)
    if (ONLY && /^slides\/[^/]+\//.test(rel) && !rel.startsWith(`slides/${ONLY}/`)) return "";
    const p = path.join(baseDir, rel);
    let content = read(p);
    // recurse so inlined files can themselves inline
    content = resolveInlines(content, path.dirname(p), depth + 1);
    return content;
  });
}

// ---- speaker-notes extractor ----
// Each slide file carries its notes in an <!-- @note … --> block directly
// above its <section>. We pull them out IN DOCUMENT ORDER (== manifest order
// == DOM order == the slide number deck.js keys on), strip them from the
// audience markup, and emit a NOTES object for the presenter console.
function extractNotes(html) {
  const notes = [];
  const noteRe = /<!--\s*@note\r?\n([\s\S]*?)(?:\r?\n)?\s*-->\s*(?=<section\b)/g;
  const stripped = html.replace(noteRe, (_, body) => { notes.push(body); return ""; });

  // Any @note not immediately followed by a <section> is a placement bug —
  // it would shift every subsequent key. Fail loudly rather than mis-key.
  const orphan = stripped.match(/<!--\s*@note\b/);
  if (orphan) throw new Error("@note block not directly above a <section> (would mis-key notes)");

  const slideCount = (stripped.match(/<section class="slide/g) || []).length;
  if (notes.length !== slideCount) {
    console.warn(`  ! notes (${notes.length}) != slides (${slideCount}) — some slides missing @note`);
  }

  const entries = notes.map((n, i) => `  ${i + 1}: ${JSON.stringify(n)}`).join(",\n");
  const block = `const NOTES = {\n${entries}\n};`;
  return { html: stripped, block };
}

function applyTokens(str) {
  // logos — full lockup (title/closer) and mark-only (small chrome slots)
  str = str.split("__LOGO_MARK_SVG__").join(LOGO_MARK_SVG);
  str = str.split("__LOGO_SVG__").join(LOGO_SVG);
  // optional Aptos Display @font-face block
  str = str.split("__FONT_FACE_DISPLAY__").join(displayFontFaces());
  // fonts
  for (const [token, file] of Object.entries(FONTS)) {
    const fp = path.join(ASSETS, "fonts", file);
    if (!fs.existsSync(fp)) { console.warn("  ! missing font:", file); continue; }
    str = str.split(token).join(b64(fp));
  }
  return str;
}

function build() {
  console.log(DECK ? `Building local deck — ${DECK}…` : ONLY ? `Building presentation — ${ONLY} slides only…` : "Building presentation…");

  // 1. main document → resolve @build:inline directives (styles, slides, js)
  let html = read(path.join(SRC, "index.html"));
  html = resolveInlines(html, SRC);

  // 2. pull speaker notes out of the slide markup → NOTES object for the console
  const { html: noteless, block } = extractNotes(html);
  if (noteless.indexOf("/* @build:notes */") === -1) console.warn("  ! @build:notes token not found");
  html = noteless.replace("/* @build:notes */", block);

  // 3. apply logo / font tokens everywhere
  html = applyTokens(html);

  // 4. sanity: no leftover build tokens
  const leftovers = html.match(/__[A-Z_]+__/g);
  if (leftovers) console.warn("  ! unreplaced tokens:", [...new Set(leftovers)].join(", "));

  fs.mkdirSync(DIST, { recursive: true });
  fs.writeFileSync(OUT, html, "utf8");

  const kb = (Buffer.byteLength(html) / 1024).toFixed(0);
  console.log(`✓ Wrote ${path.relative(ROOT, OUT)} (${kb} KB, self-contained)`);
}

build();
