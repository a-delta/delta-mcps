/* ============================================================
   SCENES — the auto-animating stages.
   ------------------------------------------------------------
   Any slide whose section carries classes "slide scene-slide" and a
   data-scene attribute is a "scene": its animations REPLAY every time
   you navigate to it (they would otherwise fire once on page load,
   before you ever reach the slide). deck.js calls Scenes.replay() on
   the active scene whenever the slide changes.

   Everything is driven by markup — no per-deck JS. Behaviours:

     • CSS entrances   any .rise/.pop/.fadein inside the scene restart.
     • KPI counters    .kpi-num[data-count] counts up; underline fills.
     • bar chart       .bar[data-val] grows its column to data-val%.
     • radial rings    .ring[data-pct] sweeps its arc; centre counts up.
     • timeline        .tl-item[data-step] light up in order; rail fills.
     • process flow    .flow-step / .flow-arrow reveal in order.
     • progress bars   .pl-row[data-val] fill their track to data-val%.
     • line chart      an SVG trend drawn from a .line-data JSON block.

   To add a bespoke scene, give it any data-scene name and drive it with
   .rise/.pop/.fadein + --d delays; the generic replay restarts them.
   ============================================================ */
(function () {

  // ---------- timed-callback bookkeeping (cleared on each replay) ----------
  const timers = [];
  const clearTimers = () => { timers.forEach(clearTimeout); timers.length = 0; };
  const later = (fn, ms) => timers.push(setTimeout(fn, ms));

  // ---------- restart every CSS entrance animation inside `root` ----------
  const RESTART_CLASSES = ["rise", "pop", "fadein"];
  function restartCss(root) {
    root.querySelectorAll(".rise,.pop,.fadein").forEach(elm => {
      const had = RESTART_CLASSES.filter(c => elm.classList.contains(c));
      had.forEach(c => elm.classList.remove(c));
      void elm.offsetWidth;            // force reflow so the animation re-runs
      had.forEach(c => elm.classList.add(c));
    });
  }

  // ---------- count a number up from 0 to target over `dur` ms ----------
  // Keeps the element's decimal places and any separators the target uses.
  function countUp(el, target, dur, done) {
    const decimals = (String(target).split(".")[1] || "").length;
    const start = performance.now();
    function frame(now) {
      const t = Math.min(1, (now - start) / dur);
      const eased = 1 - Math.pow(1 - t, 3);            // easeOutCubic
      const val = target * eased;
      el.textContent = val.toLocaleString("en-GB", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
      if (t < 1) requestAnimationFrame(frame);
      else { el.textContent = target.toLocaleString("en-GB", { minimumFractionDigits: decimals, maximumFractionDigits: decimals }); done && done(); }
    }
    requestAnimationFrame(frame);
  }

  // ---------- KPI counters ----------
  function playKpis(scene) {
    scene.querySelectorAll(".kpi").forEach((kpi, i) => {
      const num = kpi.querySelector(".kpi-num .val") || kpi.querySelector(".kpi-num");
      const target = parseFloat(kpi.querySelector(".kpi-num")?.dataset.count || "0");
      kpi.classList.remove("on");
      if (num) num.textContent = "0";
      later(() => {
        kpi.classList.add("on");
        if (num && target) countUp(num, target, 1100);
      }, 200 + i * 140);
    });
  }

  // ---------- bar chart ----------
  function playBars(scene) {
    scene.querySelectorAll(".bar").forEach((bar, i) => {
      const col = bar.querySelector(".bar-col");
      const val = Math.max(0, Math.min(100, parseFloat(bar.dataset.val || "0")));
      bar.classList.remove("on");
      if (col) col.style.height = "0%";
      later(() => { bar.classList.add("on"); if (col) col.style.height = val + "%"; }, 250 + i * 150);
    });
  }

  // ---------- radial rings ----------
  function playRings(scene) {
    scene.querySelectorAll(".ring").forEach((ring, i) => {
      const arc = ring.querySelector(".ring-arc");
      const num = ring.querySelector(".ring-num .val") || ring.querySelector(".ring-num");
      const pct = Math.max(0, Math.min(100, parseFloat(ring.dataset.pct || "0")));
      const count = parseFloat(ring.dataset.count || String(pct));
      if (arc) {
        const r = parseFloat(arc.getAttribute("r"));
        const circ = 2 * Math.PI * r;
        arc.style.strokeDasharray = circ;
        arc.style.strokeDashoffset = circ;             // start empty
        later(() => { arc.style.strokeDashoffset = circ * (1 - pct / 100); }, 250 + i * 160);
      }
      if (num) { num.textContent = "0"; later(() => countUp(num, count, 1100), 250 + i * 160); }
    });
  }

  // ---------- timeline ----------
  function playTimeline(scene) {
    const items = [...scene.querySelectorAll(".tl-item")].sort((a, b) => (+a.dataset.step || 0) - (+b.dataset.step || 0));
    const rail = scene.querySelector(".tl-rail > i");
    const n = items.length;
    items.forEach(it => it.classList.remove("lit"));
    if (rail) rail.style.setProperty("--fill", "0%");
    const STEP = 620;
    items.forEach((it, i) => {
      later(() => {
        it.classList.add("lit");
        if (rail) rail.style.setProperty("--fill", (n > 1 ? (i / (n - 1)) * 100 : 100) + "%");
      }, 400 + i * STEP);
    });
  }

  // ---------- process flow ----------
  function playFlow(scene) {
    const parts = [...scene.querySelectorAll(".flow-step, .flow-arrow")];
    parts.forEach(p => { p.classList.remove("fadein"); p.style.opacity = "0"; });
    parts.forEach((p, i) => later(() => { p.style.opacity = ""; p.classList.add("fadein"); }, 250 + i * 260));
  }

  // ---------- horizontal progress bars ----------
  function playProgress(scene) {
    scene.querySelectorAll(".pl-row").forEach((row, i) => {
      const fill = row.querySelector(".pl-track > i");
      const val = Math.max(0, Math.min(100, parseFloat(row.dataset.val || "0")));
      row.classList.remove("on");
      if (fill) fill.style.width = "0%";
      later(() => { row.classList.add("on"); if (fill) fill.style.width = val + "%"; }, 250 + i * 160);
    });
  }

  // ---------- line chart (SVG, data-driven) ----------
  // Draws the trend line on, fades in the area beneath, then pops each dot.
  const NS = "http://www.w3.org/2000/svg";
  function buildLine(scene) {
    const host = scene.querySelector(".line-chart");
    const dataEl = scene.querySelector(".line-data");
    if (!host || !dataEl) return;
    let data; try { data = JSON.parse(dataEl.textContent); } catch (e) { return; }

    const W = 1000, H = 460, padL = 56, padR = 24, padT = 20, padB = 46;
    const pts = data.points || [];                         // [{x:"Label", y:Number}]
    const ys = pts.map(p => p.y);
    const yMax = data.max != null ? data.max : Math.max(1, ...ys) * 1.1;
    const yMin = data.min != null ? data.min : 0;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const X = i => padL + (pts.length > 1 ? (i / (pts.length - 1)) * plotW : 0);
    const Y = v => padT + plotH - ((v - yMin) / (yMax - yMin)) * plotH;

    const el = (tag, attrs, parent) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; };

    let svg = host.querySelector("svg");
    if (svg) svg.remove();
    svg = el("svg", { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "xMidYMid meet" }, host);
    host.classList.remove("on");

    // horizontal gridlines + y labels (4 divisions)
    for (let g = 0; g <= 4; g++) {
      const v = yMin + (yMax - yMin) * (g / 4), y = Y(v);
      el("line", { class: "lc-grid", x1: padL, y1: y, x2: W - padR, y2: y }, svg);
      const t = el("text", { class: "lc-ylabel", x: padL - 10, y }, svg);
      t.textContent = Math.round(v).toLocaleString("en-GB");
    }
    // x axis + labels
    el("line", { class: "lc-axis", x1: padL, y1: Y(yMin), x2: W - padR, y2: Y(yMin) }, svg);
    pts.forEach((p, i) => { const t = el("text", { class: "lc-xlabel", x: X(i), y: H - padB + 26 }, svg); t.textContent = p.x; });

    // area fill under the line
    const linePath = pts.map((p, i) => `${i ? "L" : "M"}${X(i)},${Y(p.y)}`).join(" ");
    const areaPath = `${linePath} L${X(pts.length - 1)},${Y(yMin)} L${X(0)},${Y(yMin)} Z`;
    el("path", { class: "lc-area", d: areaPath }, svg);

    // the line itself — draw-on via dash offset
    const line = el("path", { class: "lc-line", d: linePath }, svg);
    const len = line.getTotalLength ? line.getTotalLength() : 1200;
    line.style.strokeDasharray = len;
    line.style.strokeDashoffset = len;

    // dots (hidden until their turn)
    const dots = pts.map((p, i) => el("circle", { class: "lc-dot", cx: X(i), cy: Y(p.y), r: 6 }, svg));

    // animate: draw line, reveal area, pop dots in sequence
    later(() => { line.style.transition = "stroke-dashoffset 1.4s var(--ease)"; line.style.strokeDashoffset = 0; host.classList.add("on"); }, 200);
    dots.forEach((d, i) => later(() => { d.style.transition = "opacity .3s var(--ease), transform .3s var(--ease)"; d.style.opacity = 1; d.style.transform = "scale(1)"; }, 400 + (i / Math.max(1, pts.length - 1)) * 1400));
  }

  // ---------- discovery scene (SVG data-flow graph) ----------
  // Reads graph data from the slide's [data-graph] JSON or falls back to
  // the built-in example. Rebuilds the SVG on every replay.
  function buildDiscovery(scene) {
    const svg = scene.querySelector(".graph-svg");
    if (!svg) return;
    svg.innerHTML = "";
    delete svg.dataset.built;

    let cfg;
    try { cfg = JSON.parse(scene.querySelector("[data-graph]")?.dataset.graph || "null"); } catch(e) { cfg = null; }
    const SOURCES = (cfg?.sources) || [
      { id:"s1", label:"View user profile",   tag:"endpoint", icon:"👤", hot:true },
      { id:"s2", label:"View an order",        tag:"endpoint", icon:"🛒" },
      { id:"s3", label:"Sign in",              tag:"endpoint", icon:"🔑" },
      { id:"s4", label:"Upload a file",        tag:"endpoint", icon:"📤" },
      { id:"s5", label:"App configuration",   tag:"file",     icon:"⚙️" },
    ];
    const MIDS = (cfg?.mids) || [
      { id:"m1", label:"Customer accounts", tag:"handler", hot:true },
      { id:"m2", label:"Orders",            tag:"handler" },
      { id:"m3", label:"Sign-in",           tag:"handler" },
      { id:"m4", label:"File handling",     tag:"module" },
    ];
    const SINKS = (cfg?.sinks) || [
      { id:"k1", label:"Customer database",  tag:"sink", hot:true },
      { id:"k2", label:"Grocery orders",     tag:"sink" },
      { id:"k3", label:"Sign-in & sessions", tag:"sink" },
      { id:"k4", label:"Uploaded files",     tag:"sink" },
      { id:"k5", label:"Secrets / config",   tag:"sink" },
    ];
    const EDGES   = (cfg?.edges)    || [["s1","m1"],["s2","m2"],["s3","m3"],["s4","m4"],["s5","m1"],["m1","k1"],["m1","k5"],["m2","k2"],["m3","k3"],["m4","k4"],["m2","k1"]];
    const HOTPATH = (cfg?.hotPath)  || [["s1","m1"],["m1","k1"]];
    const HOT_EDGES = new Set(HOTPATH.map(([a,b]) => a+">"+b));
    const LABELS  = (cfg?.labels)   || ["Where data comes in","Our application","Sensitive data"];
    const COL_IDS = ["s","m","k"];

    const W=1000, H=560, NW=200, NH=40;
    const COLX = {s:40, m:400, k:760};
    const cx = {s:COLX.s+NW/2, m:COLX.m+NW/2, k:COLX.k+NW/2};

    function layout(list, col) {
      const top=96, usable=H-top-40, gap=usable/list.length, map={};
      list.forEach((n,i) => { const y=top+gap*i+(gap-NH)/2; map[n.id]={...n, x:COLX[col], y, cxp:cx[col], cyp:y+NH/2, col}; });
      return map;
    }
    const el = (tag,attrs,parent) => { const e=document.createElementNS(NS,tag); for(const k in attrs) e.setAttribute(k,attrs[k]); if(parent) parent.appendChild(e); return e; };

    const S=layout(SOURCES,"s"), M=layout(MIDS,"m"), K=layout(SINKS,"k");
    const nodes={...S,...M,...K};

    COL_IDS.forEach((c,i) => { const t=el("text",{class:"g-col-label",x:cx[c],y:66},svg); t.textContent=LABELS[i]||c; });

    let edgeDelay=0.5;
    EDGES.forEach(([a,b],i) => {
      const na=nodes[a], nb=nodes[b]; if(!na||!nb) return;
      const x1=na.x+NW, y1=na.cyp, x2=nb.x, y2=nb.cyp, dx=(x2-x1)*0.5;
      const d=`M${x1},${y1} C${x1+dx},${y1} ${x2-dx},${y2} ${x2},${y2}`;
      const p=el("path",{class:"g-edge",d},svg);
      const len=Math.hypot(x2-x1,y2-y1)+Math.abs(dx);
      p.style.setProperty("--len",Math.round(len*1.25));
      p.style.setProperty("--d",(edgeDelay+i*0.08).toFixed(2)+"s");
      if(HOT_EDGES.has(a+">"+b)) p.classList.add("hot");
    });

    const colDelay={s:0.1, m:1.0, k:1.7};
    Object.values(nodes).forEach((n,i) => {
      const g=el("g",{class:"g-node "+n.col+(n.hot?" hot":"")},svg);
      g.style.setProperty("--d",(colDelay[n.col]+(i%5)*0.07).toFixed(2)+"s");
      el("rect",{class:"nbox",x:n.x,y:n.y,width:NW,height:NH,rx:9},g);
      if(n.icon) { const ic=el("text",{class:"nicon",x:n.x+14,y:n.cyp},g); ic.textContent=n.icon; }
      const t=el("text",{class:n.plain?"nlabel-plain":"",x:n.x+(n.icon?40:12),y:n.cyp},g);
      t.textContent=n.label;
    });

    HOTPATH.forEach(([a,b],i) => {
      const na=nodes[a], nb=nodes[b]; if(!na||!nb) return;
      const x1=na.x+NW, y1=na.cyp, x2=nb.x, y2=nb.cyp, dx=(x2-x1)*0.5;
      const d=`M${x1},${y1} C${x1+dx},${y1} ${x2-dx},${y2} ${x2},${y2}`;
      const c=el("circle",{class:"g-spark",r:5},svg);
      c.style.setProperty("--sd",(2.4+i*0.5)+"s");
      el("animateMotion",{dur:"1.3s",repeatCount:"indefinite",path:d,begin:(2.4+i*0.5)+"s"},c);
    });
  }

  // ---------- hunt scene (parallel specialist swarm) ----------
  function playHunt(scene) {
    scene.querySelectorAll(".spec-card").forEach(card => {
      card.classList.remove("clear","hit");
      const st=card.querySelector(".sc-status"); if(st) st.textContent="scanning…";
      const ms=parseFloat(card.dataset.rd||"1.8")*1000;
      later(() => {
        const res=card.dataset.resolve;
        card.classList.add(res);
        if(st) st.textContent=res==="hit"?"⚠ flaw found":"clear";
      }, ms);
    });
  }

  // ---------- custom scene registry ----------
  // A local deck adds bespoke scenes via window.Scenes.register("name", fn)
  // from its .local/slides/<deck>/local.js — no need to fork this file.
  const custom = {};
  const register = (kind, fn) => { custom[kind] = fn; };

  // ---------- replay controller ----------
  function replay(scene) {
    if (!scene) return;
    clearTimers();
    restartCss(scene);

    const kind = scene.dataset.scene;
    if (kind === "kpi") playKpis(scene);
    else if (kind === "bars") playBars(scene);
    else if (kind === "rings") playRings(scene);
    else if (kind === "timeline") playTimeline(scene);
    else if (kind === "flow") playFlow(scene);
    else if (kind === "progress") playProgress(scene);
    else if (kind === "line") buildLine(scene);
    else if (kind === "discovery") buildDiscovery(scene);
    else if (kind === "hunt") playHunt(scene);
    else if (custom[kind]) custom[kind](scene);
    // any other data-scene value just gets the generic CSS-entrance restart.
  }

  // Publish synchronously so a local.js inlined right after this file can
  // register its scenes before DOMContentLoaded fires.
  window.Scenes = { replay, register };

  function init() {
    const active = document.querySelector(".slide.active[data-scene]");
    if (active) replay(active);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
