/* ============================================================
   EDIT MODE — toggle contenteditable on slide text, then copy
   the edited <section> back to its own per-slide source file.

   The deck is split into one file per slide under
   src/slides/<part>/<topic>.html, wired together by the manifest
   src/slides.html. Each <section> carries a data-file="<part>/<topic>.html"
   attribute naming its source. Edit mode is inherently per-slide,
   so copy exports ONLY the active slide — it can never flatten the
   split back into one blob.

   Usage:
     1. Press the "Edit" button (bottom-left) to enter edit mode.
     2. Click any text on the current slide and type.
     3. Press "Copy slide" — the active <section> is serialised and
        copied to the clipboard, and the button names its target file.
     4. Paste it over that file (src/slides/<part>/<topic>.html)
        and run: node build.js
     5. Navigate to another slide to edit it; copy again.

   Only text-bearing elements get contenteditable so structural
   markup, SVGs, and data attributes are preserved.
   ============================================================ */
(function () {
  const EDITABLE = "h1,h2,h3,p,li,.kicker,.lead,.analogy,.subtitle,.tag-line,.f2-line,.f2-tail,.fix2-human span:last-child,.fix2-code";

  let active = false;

  function enable() {
    document.querySelectorAll("#deck .slide").forEach(slide => {
      slide.querySelectorAll(EDITABLE).forEach(el => {
        el.contentEditable = "true";
        el.spellcheck = false;
      });
    });
    document.documentElement.classList.add("edit-mode");
    active = true;
    btn.textContent = "Copy slide";
    btn.title = "Copy the active slide's markup to its source file";
  }

  function disable() {
    document.querySelectorAll("#deck .slide").forEach(slide => {
      slide.querySelectorAll(EDITABLE).forEach(el => {
        el.removeAttribute("contenteditable");
      });
    });
    document.documentElement.classList.remove("edit-mode");
    active = false;
    btn.textContent = "Edit";
    btn.title = "Enter edit mode";
  }

  function svgSubstitutions() {
    const subs = [];
    const grab = (sel, placeholder) => {
      const el = document.querySelector(sel);
      if (el && el.innerHTML.trim()) subs.push([el.innerHTML.trim(), placeholder]);
    };
    const t = (s) => "__" + s + "__";
    grab(".title-logo svg",  t("LOGO_SVG"));
    grab(".logo-mark svg",   t("LOGO_MARK_SVG"));
    return subs;
  }

  // The slide currently on screen (deck.js marks it .active); fall back
  // to the first slide if edit mode is somehow entered before render.
  function activeSlide() {
    return document.querySelector("#deck .slide.active")
        || document.querySelector("#deck .slide");
  }

  // Serialise ONE slide's <section>, restoring inline SVGs to their
  // build tokens so the output pastes straight back over the source.
  function extractSlide(slide) {
    const clone = slide.cloneNode(true);
    clone.classList.remove("active");
    clone.querySelectorAll(EDITABLE).forEach(el => el.removeAttribute("contenteditable"));
    let markup = clone.outerHTML;
    svgSubstitutions().forEach(([svg, placeholder]) => {
      markup = markup.split(svg).join(placeholder);
    });
    return markup;
  }

  function flash(label) {
    btn.textContent = label;
    btn.classList.add("copied");
    setTimeout(() => {
      btn.textContent = "Copy slide";
      btn.classList.remove("copied");
    }, 2400);
  }

  function copyActiveSlide() {
    const slide = activeSlide();
    if (!slide) return;
    const file = slide.getAttribute("data-file");
    const target = file ? ("slides/" + file) : "its source file";
    const markup = extractSlide(slide);
    const done = () => flash("Copied → " + target + " ✓");
    navigator.clipboard.writeText(markup).then(done).catch(() => {
      // fallback for browsers that block clipboard without interaction
      const ta = document.createElement("textarea");
      ta.value = markup;
      ta.style.cssText = "position:fixed;opacity:0;pointer-events:none;";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      done();
    });
  }

  const btn = document.createElement("button");
  btn.id = "btnEdit";
  btn.className = "btn";
  btn.textContent = "Edit";
  btn.title = "Enter edit mode";

  btn.addEventListener("click", () => {
    if (!active) {
      enable();
    } else {
      copyActiveSlide();
    }
  });

  // Esc exits edit mode
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && active) {
      disable();
    }
  });

  function mountBtn() {
    const controls = document.getElementById("controls");
    if (controls) controls.prepend(btn);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mountBtn);
  else mountBtn();
})();
