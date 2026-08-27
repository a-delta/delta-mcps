/* ============================================================
   DECK CONTROLLER
   ------------------------------------------------------------
   One document, three modes (set on <html data-mode>):

     "deck"     standalone — just open the file, navigate normally.
     "present"  the popup opened by pressing P. Shows the slides
                fullscreen for the projector. Pure renderer.
     "console"  the ORIGINAL window after pressing P. Shows speaker
                notes, timing and controls on your laptop. It is the
                "brain": all navigation state lives here.

   Windows sync with window.opener + postMessage (targetOrigin "*"),
   which works over file:// — unlike BroadcastChannel, which treats
   every file:// page as a different origin and never connects.

   Only the brain (console / standalone) mutates state. The present
   window forwards key presses to the console and renders whatever
   state it receives back. No split-brain, no drift.
   ============================================================ */
const Deck = (() => {

  // ---- SECTIONS: name + time budget (minutes) per data-section index.
  //      The examples default is below; a local deck overrides it by setting
  //      window.DECK_SECTIONS (in its .local/slides/<deck>/local.js). ----
  const SECTIONS = window.DECK_SECTIONS || [
    { name: "Layouts",      budget: 4 * 60 },
    { name: "Data scenes",  budget: 7 * 60 },
    { name: "Text & close", budget: 4 * 60 },
    { name: "Showcase",     budget: 10 * 60 },
  ];

  const slides = [...document.querySelectorAll(".slide")];
  const $ = id => document.getElementById(id);

  let mode = "deck";
  let presentWin = null;            // (console) handle to the presentation popup
  let lastRenderedIndex = -1;       // so scenes replay only on slide change

  // ---- unified navigation state (owned by the brain) ----
  const S = { index: 0, fragStep: 0, theme: "light" };

  // ---- timing (brain only) — one accumulator per section ----
  const sectionElapsed = SECTIONS.map(() => 0);
  let totalElapsed = 0, lastTick = null;

  // ---- helpers ----
  const sectionOf = i => Math.min(SECTIONS.length - 1, parseInt(slides[i].dataset.section || "0", 10));
  const fragEls = i => [...slides[i].querySelectorAll("[data-frag]")];
  // fragSteps: ordered list of unique step keys (group name, or element index when ungrouped)
  function fragSteps(i) {
    const seen = [];
    fragEls(i).forEach((el, k) => {
      const g = el.dataset.fragGroup || String(k);
      if (!seen.includes(g)) seen.push(g);
    });
    return seen;
  }
  const fragCount = i => fragSteps(i).length;
  const fmt = s => { s = Math.max(0, Math.round(s)); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); };
  function titleOf(i) {
    const el = slides[i].querySelector("h1,h2");
    return el ? el.textContent.replace(/\.$/, "").trim() : "Slide " + (i + 1);
  }

  // =========================================================
  //  RENDER
  // =========================================================
  function render() {
    document.documentElement.setAttribute("data-theme", S.theme);

    const slideChanged = S.index !== lastRenderedIndex;

    // show the active slide
    slides.forEach((s, k) => s.classList.toggle("active", k === S.index));

    // reveal fragments up to the current step
    const steps = fragSteps(S.index);
    fragEls(S.index).forEach(el => {
      const g = el.dataset.fragGroup || String(fragEls(S.index).indexOf(el));
      el.classList.toggle("in", steps.indexOf(g) < S.fragStep);
    });

    // replay an auto-animating scene when we land on it (not on every render,
    // and not in the console window where slides are hidden)
    if (slideChanged && mode !== "console" && typeof Scenes !== "undefined") {
      if (slides[S.index].dataset.scene !== undefined) Scenes.replay(slides[S.index]);
    }
    lastRenderedIndex = S.index;

    updateChrome();
    if (mode === "console") renderConsole();
  }

  function updateChrome() {
    const sec = sectionOf(S.index);
    if ($("cSect")) $("cSect").textContent = sec + 1;
    if ($("cNum")) $("cNum").textContent = S.index + 1;
    if ($("cTot")) $("cTot").textContent = slides.length;
    if ($("progress")) $("progress").firstElementChild.style.width = (S.index / Math.max(1, slides.length - 1) * 100) + "%";
    if ($("sectName")) $("sectName").textContent = SECTIONS[sec].name;
    if (S.index > 0 && $("hint")) $("hint").classList.add("hide");
  }

  function renderConsole() {
    const sec = sectionOf(S.index);
    $("conSect").textContent = sec + 1;
    $("conIdx").textContent = (S.index + 1) + " / " + slides.length;
    $("conSectName").textContent = SECTIONS[sec].name;
    $("conNotes").textContent = (typeof NOTES !== "undefined" && NOTES[S.index + 1]) || "— no notes for this slide —";

    const hasNext = S.index + 1 < slides.length;
    $("conNextTitle").textContent = hasNext ? titleOf(S.index + 1) : "End of deck";
    $("conNextNotes").textContent = hasNext ? ((typeof NOTES !== "undefined" && NOTES[S.index + 2]) || "") : "";

    // timing
    const el = sectionElapsed[sec], bud = SECTIONS[sec].budget, over = el > bud;
    const te = $("conElapsed");
    te.textContent = fmt(el); te.classList.toggle("over", over);
    $("conBudget").textContent = fmt(bud);
    const bar = $("conBar");
    bar.style.width = Math.min(100, el / bud * 100) + "%";
    bar.classList.toggle("over", over);
    $("conRemain").textContent = over ? ("over by " + fmt(el - bud)) : (fmt(bud - el) + " left in section");
    $("conTotal").textContent = "total " + fmt(totalElapsed);

    // progress within slide
    $("conReveals").innerHTML = "Step reveals: <b>" + S.fragStep + " / " + fragCount(S.index) + "</b> on this slide";
    $("conDemo").style.display = "none";

    // connection status
    const st = $("conStatus");
    if (presentWin && !presentWin.closed) { st.textContent = "● presentation window open"; st.className = "status"; }
    else { st.textContent = "presentation window closed — click ↗ Presentation to reopen"; st.className = "status warn"; }
  }

  // =========================================================
  //  NAVIGATION (brain only — present forwards to us)
  // =========================================================
  function stepForward() {
    if (S.fragStep < fragCount(S.index)) { S.fragStep++; return true; }
    return false; // exhausted this slide
  }
  function stepBack() {
    if (S.fragStep > 0) { S.fragStep--; return true; }
    return false;
  }
  function enterSlide(i, atEnd) {
    S.index = Math.max(0, Math.min(slides.length - 1, i));
    S.fragStep = atEnd ? fragCount(S.index) : 0;
  }

  function next() {
    if (!stepForward()) { if (S.index < slides.length - 1) enterSlide(S.index + 1, false); }
    commit();
  }
  function prev() {
    if (!stepBack()) { if (S.index > 0) enterSlide(S.index - 1, true); }
    commit();
  }
  function goto(i) { enterSlide(i, false); commit(); }
  function gotoEnd() { enterSlide(slides.length - 1, true); commit(); }

  function toggleTheme() {
    S.theme = S.theme === "dark" ? "light" : "dark";
    try { localStorage.setItem("deck-theme", S.theme); } catch (e) {}
    commit();
  }

  // Apply state change: render locally + push to the presentation window.
  function commit() { render(); pushState(); }

  // =========================================================
  //  CROSS-WINDOW SYNC
  // =========================================================
  function pushState() {
    if (mode !== "console") return;
    if (presentWin && !presentWin.closed) {
      try { presentWin.postMessage({ t: "state", s: { index: S.index, fragStep: S.fragStep, theme: S.theme } }, "*"); } catch (e) {}
    }
  }
  // present → console intents
  function sendToBrain(msg) {
    try { if (window.opener && !window.opener.closed) window.opener.postMessage(msg, "*"); } catch (e) {}
  }

  function onMessage(e) {
    const m = e.data || {};
    if (mode === "present") {
      if (m.t === "state") { Object.assign(S, m.s); render(); }
    } else { // console / deck acting as brain
      if (m.t === "ready") pushState();
      else if (m.t === "nav") { m.dir === "prev" ? prev() : next(); }
      else if (m.t === "theme") toggleTheme();
      else if (m.t === "goto") goto(m.i);
      else if (m.t === "end") gotoEnd();
      else if (m.t === "exit") closePresent();
    }
  }

  // =========================================================
  //  PRESENTER MODE
  // =========================================================
  function openPresent() {
    if (presentWin && !presentWin.closed) { presentWin.focus(); return; }
    const url = location.href.split("#")[0] + "#present";
    presentWin = window.open(url, "deck-present",
      "width=1280,height=800,menubar=no,toolbar=no,location=no,status=no");
    if (!presentWin) { alert("Please allow pop-ups for this page, then press P again.\n\nThe presentation opens in a new window you can drag to the projector and fullscreen (F)."); return; }
    // This window becomes the console.
    mode = "console";
    document.documentElement.setAttribute("data-mode", "console");
    render();
    setTimeout(pushState, 400);
  }

  function closePresent() {
    if (presentWin && !presentWin.closed) { try { presentWin.close(); } catch (e) {} }
    presentWin = null;
    mode = "deck";
    document.documentElement.setAttribute("data-mode", "deck");
    render();
  }

  // =========================================================
  //  INPUT
  // =========================================================
  function keys(e) {
    if (document.activeElement && document.activeElement.isContentEditable) return;
    // present window: forward navigation to the brain; handle fullscreen locally
    if (mode === "present") {
      switch (e.key) {
        case "ArrowRight": case " ": case "PageDown": e.preventDefault(); sendToBrain({ t: "nav", dir: "next" }); break;
        case "ArrowLeft": case "PageUp": e.preventDefault(); sendToBrain({ t: "nav", dir: "prev" }); break;
        case "Home": e.preventDefault(); sendToBrain({ t: "goto", i: 0 }); break;
        case "End": e.preventDefault(); sendToBrain({ t: "end" }); break;
        case "t": case "T": sendToBrain({ t: "theme" }); break;
        case "f": case "F": toggleFs(); break;
        case "Escape": if (!document.fullscreenElement) sendToBrain({ t: "exit" }); break;
      }
      return;
    }
    // deck / console (brain)
    switch (e.key) {
      case "ArrowRight": case " ": case "PageDown": e.preventDefault(); next(); break;
      case "ArrowLeft": case "PageUp": e.preventDefault(); prev(); break;
      case "Home": e.preventDefault(); goto(0); break;
      case "End": e.preventDefault(); gotoEnd(); break;
      case "t": case "T": toggleTheme(); break;
      case "p": case "P": openPresent(); break;
      case "l": case "L": if (typeof Laser !== "undefined") Laser.toggle(); break;
      case "f": case "F": toggleFs(); break;
      case "?": $("help") && $("help").classList.toggle("show"); break;
      case "Escape":
        if ($("help") && $("help").classList.contains("show")) { $("help").classList.remove("show"); }
        else if (mode === "console") { closePresent(); }
        break;
    }
  }
  function toggleFs() {
    if (!document.fullscreenElement) document.documentElement.requestFullscreen?.();
    else document.exitFullscreen?.();
  }

  // =========================================================
  //  TIMERS (brain only)
  // =========================================================
  function tick() {
    const now = Date.now();
    if (lastTick != null) {
      const dt = (now - lastTick) / 1000;
      sectionElapsed[sectionOf(S.index)] += dt;
      totalElapsed += dt;
    }
    lastTick = now;
    if (mode === "console") renderConsole();
    else if (mode === "deck") {
      const sec = sectionOf(S.index), el = sectionElapsed[sec];
      const t = $("sectTimer");
      if (t) { t.textContent = fmt(el); t.classList.toggle("over", el > SECTIONS[sec].budget); }
    }
  }

  // =========================================================
  //  INIT
  // =========================================================
  function init() {
    try { S.theme = localStorage.getItem("deck-theme") || "light"; } catch (e) {}

    if (location.hash.indexOf("present") !== -1) {
      mode = "present";
      document.documentElement.setAttribute("data-mode", "present");
    }

    window.addEventListener("message", onMessage);
    document.addEventListener("keydown", keys);

    if (mode !== "present") {
      // brain wiring (works for standalone deck and, after P, console)
      $("btnNext").onclick = next;
      $("btnPrev").onclick = prev;
      $("btnTheme").onclick = toggleTheme;
      $("btnPresent").onclick = openPresent;
      $("btnHelp").onclick = () => $("help").classList.toggle("show");
      if ($("btnLaser")) $("btnLaser").onclick = () => { if (typeof Laser !== "undefined") Laser.toggle(); };
      $("help").addEventListener("click", e => { if (e.target.id === "help") $("help").classList.remove("show"); });
      // console controls
      $("conNext").onclick = next;
      $("conPrev").onclick = prev;
      $("conTheme").onclick = toggleTheme;
      $("conReopen").onclick = openPresent;
      $("conExit").onclick = closePresent;

      lastTick = Date.now();
      setInterval(tick, 1000);
    } else {
      // present window: announce readiness so the console sends current state
      sendToBrain({ t: "ready" });
      setTimeout(() => sendToBrain({ t: "ready" }), 300);
      setTimeout(() => sendToBrain({ t: "ready" }), 900);
    }

    render();
  }

  return { init };
})();

document.addEventListener("DOMContentLoaded", Deck.init);
