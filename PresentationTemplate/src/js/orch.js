/* ============================================================
   ORCHESTRATOR ENGINE — one orchestrator delegating to parallel
   sub-agents on a virtual run-clock (slide: part3-ir/orchestrator.html).

   A virtual run-clock drives everything: agents fade in on their spawn
   time, run (own live scrolling feed), then a return-wire pulses and
   stateless nodes fade; stateful (warm) nodes stay and can be resumed.

   Mirrors the timeline module contract: the deck sets a discrete "stop"
   (one per bookmark) via setState(); on top of that the slide has its
   own audience-surface controls — draggable playhead, Back/Next between
   bookmarks, Play/pause, speed 1×/2×/4× — which report the nearest
   bookmark back via onScrub so both windows stay in sync.

   Representative of the ModHeader case (ticket 2628635); telemetry
   split into smaller concurrent strands.
   ============================================================ */
const Orch = (() => {
  "use strict";
  const SPAN = 52, RET = 3.4;   // virtual seconds; RET = how long a return pulse lingers
  const REAL_TOTAL = 66, REAL_START = 13*60+13;   // 66 real minutes, from 13:13
  const realClock = t => { const m = REAL_START + Math.round(t/SPAN*REAL_TOTAL); return String(Math.floor(m/60)).padStart(2,"0")+":"+String(m%60).padStart(2,"0"); };
  const fmtDur = mins => { mins=Math.max(0,mins); return Math.floor(mins/60)+"h "+String(mins%60).padStart(2,"0")+"m"; };
  const LEAD = { think:"Preparing", reason:"Reasoning", dispatch:"Despatching", verdict:"Verdict" };

  /* bookmarks = Back/Next targets + track ticks + deck stops */
  const BOOK = [
    { t:2,    lbl:"loads skill" },
    { t:7,    lbl:"web research" },
    { t:15,   lbl:"read the KB ×2" },
    { t:22,   lbl:"sweep estate" },
    { t:32,   lbl:"wider net ×2" },
    { t:44,   lbl:"pivot ×2" },
    { t:50,   lbl:"verdict" },
  ];
  const VERDICT = BOOK[BOOK.length-1].t;   // once reached, the investigation is over

  /* orchestrator feed — heavily summarised, high level */
  const ORCH = [
    { t:0.4, c:"think",    p:"💭 Loaded <b>/investigate-alert</b> skill, reading its rules I must stick to.",
      detail:{ h:"What it's been asked to do", bullets:[
        "Investigate the alert fully and give a person a verdict of <b>benign, malicious, or escalate</b>, backed by evidence.",
        "<b>It never closes the ticket.</b> A person acts on the recommendation.",
        "<b>Scratch mode:</b> the analyst's own notes are set aside, so the verdict is based on the raw alert alone, not on what someone already concluded.",
        "Every earlier statement is treated as an <b>unverified claim</b> until it has been checked.",
      ] } },
    { t:1.6, c:"think",    p:"Reading the investigation principles from the knowledge base before I start any analysis.",
      detail:{ h:"The rules it has to follow", bullets:[
        "<b>Evidence, not opinion:</b> a previous conclusion is a claim, not proof. Only checking it closes the question.",
        "<b>Explanations first:</b> list the possible explanations and rule them out with evidence, not a hunch.",
        "<b>Outcome must be evidenced:</b> deciding it's benign needs as much evidence as deciding it's malicious.",
        "<b>Mind the gaps:</b> no data does not mean no problem. Check for missing telemetry before clearing anything.",
      ], leaves:["verify-before-precedent gates","symmetric evidential bar","investigation framework","environmental preconditions"] } },
    { t:3.0, c:"reason",   p:"The user said that Google Web Store flagged ModHeader as malware. Is this accurate, and what should I look for?" },
    { t:3.6, c:"dispatch", p:"⇉ Send a web researcher to read the public report, confirm the user's assertions, and pull the indicators and malware behaviours to search for." },
    { t:9.5, c:"reason",   p:"The research confirms it: a genuine supply chain compromise, and I now have the indicators to hunt." },
    { t:11,  c:"dispatch", p:"⇉ Now checking what we already know: Despatching two agents to read the knowledge base and look for relevant information." },
    { t:17,  c:"reason",   p:"No specific playbook for malicious extensions, I'll start with understanding coverage before recommending actions." },
    { t:18,  c:"dispatch", p:"⇉ Despatch a telemetry agent to tell me where the browser add on is installed across our estate." },
    { t:26,  c:"reason",   p:"Found it on 84 devices so far, now to see if we can find evidence of any data being stolen." },
    { t:27,  c:"dispatch", p:"⇉ Widen the search, despatch 2 agents at once: one on the endpoints, one on the web traffic." },
    { t:37,  c:"reason",   p:"The web traffic shows <span class='big'>386</span> devices reached the attacker's infrastructure, far more than what I initially found." },
    { t:40,  c:"reason",   p:"One device was allowed through to the data theft domain. I need to investigate that before I can reach a verdict." },
    { t:40.6,c:"dispatch", p:"⇉ Search the web proxy for exactly what that device did, and I'll ask the other agent to check the traffic to the Command &amp; Control domains." },
    { t:47,  c:"reason",   p:"Results back: just one visit to a webpage, not malicious, and no traffic to the Command &amp; Control domains." },
    { t:49,  c:"verdict",  p:"⚖️ Verdict: escalate. 386 installations of the compromised browser extension but no evidence of data being stolen. Recommend blocking the websites and uninstalling the extension." },
  ];

  /* agents. windows = [[t0,t1], ...resume]. feed times are ABSOLUTE virtual seconds. */
  const AGENTS = [
    { id:"web", kind:"web", col:"mid", persist:false, win:[[3.8,9]], name:"web-researcher", tier:"opus · stateless",
      ask:"Is this real? What should we hunt for?", src:"Web · the researcher's report", reportAt:6,
      feed:[[4,"opening the researcher's blog post"],[4.6,"cross-checking Google's signed store build"],[8.2,"verified real · 900k-user extension · pulled the IOCs","ok"]],
      report:{
        name:"ModHeader · Chrome extension",
        usage:"900k+ users · now pulled from the Web Store",
        caps:[
          "Monitors websites visited",
          "Pings back to attacker",
          "Remote switch to start theft",
        ],
        iocs:[
          ["exfil","api.stanfordstudies[.]com"],
          ["telemetry","extensions-hub[.]com"],
          ["ip","3.147.61[.]167"],
          ["ext id","idgpnmonknjnojd…"],
        ],
      } },
    { id:"kb1", kind:"kb", col:"mid", persist:false, win:[[12,16]], name:"kb-retriever", tier:"opus · stateless",
      ask:"Do we have a playbook for a malicious extension?",
      feed:[[12.4,"searching our response playbooks"],[14.5,"no specific playbook for extensions","warn"],[15.5,"closest guidance: blocklist and coordinate removal","ok"]] },
    { id:"kb2", kind:"kb", col:"mid", persist:false, win:[[12.5,17]], name:"kb-retriever", tier:"opus · stateless",
      ask:"Have we seen this extension before?",
      feed:[[12.9,"checking prior incidents + our estate notes"],[16,"no prior report — this is new to us","ok"]] },
    { id:"telA", kind:"tel", col:"mid", persist:false, win:[[19,26]], name:"telemetry-searcher", tier:"opus · stateless",
      ask:"Installed where, which version?", src:"Databricks · endpoint records",
      feed:[[19.2,"📚 checking the knowledge base for the data map"],[19.5,"querying endpoint records"],[22,"counting installs by version"],[24.5,"84 devices — 79 with the data store","ok"]] },
    { id:"telB", kind:"tel", col:"mid", persist:false, win:[[28,35]], name:"telemetry-searcher", tier:"opus · stateless",
      ask:"Did any endpoint hit the attacker's domains?", src:"Databricks · endpoint network",
      feed:[[28.5,"checking outbound connections"],[31,"filtering to attacker domains"],[33.5,"a few touched telemetry infra; none hit the theft endpoint","ok"]] },
    { id:"telC", kind:"tel", col:"mid", persist:true, win:[[28.5,37],[41,47]], name:"telemetry-searcher", tier:"opus · persisted (warm)",
      ask:"Who reached attacker infra via the web proxy?", src:"Splunk · Zscaler proxy",
      feed:[[28.7,"📚 checking the knowledge base — how the proxy logs are shaped"],[29,"searching 90 days of proxy logs"],[33,"counting who reached attacker infra"],[36,"386 devices / 409 users; 1 lone allowed theft-domain hit","ok"],
            [41.4,"resumed — cross-checking the C2 subdomains"],[45.5,"no proxy traffic to any C2 subdomain","ok"]] },
    { id:"telD", kind:"tel", col:"mid", persist:false, win:[[41,48]], name:"telemetry-searcher", tier:"opus · stateless",
      ask:"What did that one host actually do?", src:"Databricks · endpoint",
      feed:[[41.5,"pulling that host's activity"],[46.5,"just one user's own homepage/logo fetch — not exfil","ok"]] },
  ];
  const ALL = [...AGENTS];

  const PROG = [
    { s:0.5, e:3,  name:"Understanding the alert" },
    { s:3,  e:9,  name:"Researching the threat" },
    { s:15, e:26, name:"Initial device search" },
    { s:27, e:37, name:"Wider search" },
    { s:40, e:47, name:"Assessing the findings" },
    { s:48, e:50, name:"Reaching a verdict" },
  ];

  /* counter keyframes [t, searches, findings, kbreads] -> interpolated */
  const CK = [[0,0,0,0],[3,0,0,0],[9,1,3,0],[17,1,5,4],[26,3,7,6],[35,6,10,7],[37,6,11,7],[48,9,15,8],[52,9,15,8]];
  function counters(now){
    let a=CK[0];
    for (let i=0;i<CK.length-1;i++){ if(now<=CK[i+1][0]){ a=CK[i]; const b=CK[i+1]; const f=(now-a[0])/(b[0]-a[0]);
      return [0,1,2].map(j=>Math.round(a[j+1]+f*(b[j+1]-a[j+1]))); } }
    return CK[CK.length-1].slice(1);
  }

  /* state of an agent at time now: none | running | returning | warm | done */
  function stateAt(a, now){
    const W=a.win;
    if (now < W[0][0]) return "none";
    // Verdict reached: the investigation is over, so a persisted (warm) agent that
    // has finished its last window collapses to Done rather than lingering warm.
    if (a.persist && now>=VERDICT && now>=W[W.length-1][1]) return "done";
    for (const w of W){ if (now>=w[0] && now<w[1]) return "running"; if (now>=w[1] && now<w[1]+RET) return "returning"; }
    const lastEnd = W[W.length-1][1]+RET;
    if (now>=lastEnd) return a.persist ? "warm" : "done";
    return "warm"; // gap between windows (persisted, awaiting resume)
  }

  const bigify = t => String(t).replace(/386/g, "<span class='big'>386</span>");

  // cached DOM (built once in mount)
  const E = {};
  let built = false;
  let now=0, playing=false, speed=1, lastFrame=0, rafId=null;
  let lastPushed=-1;   // last deck stop reported via onScrub, so Play/scrub keep the deck in sync
  const nodeEls = new Map();   // id -> {agent, root, mode, feedEl, ledEl, cardEl, state, shown:Set}
  let activeOf={}, doneListOf={}, doneWrapOf={};
  let onScrub = null, onPlay = null;

  function cardMarkup(a){
    const icon=a.kind==="kb"?"📚":a.kind==="web"?"🔎":"🔭";
    const src=a.src?`<div class="c-src">${a.src}</div>`:"";
    const rep=a.report?`<div class="c-report" hidden></div>`:"";
    return `<div class="wire"></div><div class="card">
      <div class="c-head"><span class="c-ico">${icon}</span>
        <div><div class="c-name">${a.name}</div><div class="c-tier">${a.tier}</div></div>
        <span class="c-led"></span></div>
      <div class="c-ask">${a.ask}</div>${src}${rep}
      <div class="c-feed"></div></div>`;
  }
  function detailHtml(d){
    const bl=d.bullets.map(b=>`<li>${b}</li>`).join("");
    const leaves=d.leaves?`<div class="dh">Must-read doctrine</div><div class="leaves">${d.leaves.map(l=>`<span class="leaf">${l}</span>`).join("")}</div>`:"";
    return `<div class="dh">${d.h}</div><ul>${bl}</ul>${leaves}`;
  }
  function reportHtml(r){
    const caps=r.caps.map(c=>`<li>${c}</li>`).join("");
    const iocs=r.iocs.map(([k,v])=>`<div class="ioc"><span class="k">${k}</span><span class="v">${v}</span></div>`).join("");
    return `<div class="rr-name">${r.name}</div><span class="rr-usage">${r.usage}</span>
      <div class="rr-cols">
        <div class="rr-col"><div class="rr-h">What it does</div><ul>${caps}</ul></div>
        <div class="rr-col"><div class="rr-h">Indicators to hunt · defanged</div>${iocs}</div>
      </div>`;
  }
  function doneResult(a){ let r=null; for(const f of a.feed){ if(f[2]==="ok") r=f[1]; } return r || a.feed[a.feed.length-1][1]; }
  function chipMarkup(a){
    const icon=a.kind==="kb"?"📚":a.kind==="web"?"🔎":"🔭";
    return `<span class="ci">${icon}</span><span class="cn">${a.name}</span><span class="cr">${bigify(doneResult(a))}</span>`;
  }

  function ensureEl(a, mode, animate){
    let e=nodeEls.get(a.id);
    if (e && e.mode===mode) return e;
    let flip=null, ghostSrc=null;
    if (e){
      // Card → chip: snapshot the live card so it can shrink into the Done pile.
      if(e.mode==="card" && mode==="chip" && animate){ flip=e.root.getBoundingClientRect(); ghostSrc=e.root.cloneNode(true); }
      e.root.remove();
    }
    const root=document.createElement("div");
    if (mode==="chip"){
      root.className=`chip ${a.kind}`+((animate&&!flip)?" new":"");
      root.innerHTML=chipMarkup(a);
      doneListOf[a.col].prepend(root);          // newest at the top of the Done pile
      doneWrapOf[a.col].hidden=false;
      if (flip && ghostSrc){   // the live card shrinks (font and all) down into the pile
        const nr=root.getBoundingClientRect();  // the chip's landing spot
        root.style.opacity="0";                 // hide the real chip until the ghost lands
        const ghost=ghostSrc;                   // full, still-styled card clone
        ghost.classList.add("folding");
        // Kept inside .s5 so it keeps every card style; fixed-positioned over the card.
        ghost.style.cssText=`position:fixed;left:${flip.left}px;top:${flip.top}px;width:${flip.width}px;height:${flip.height}px;margin:0;z-index:9999;transform-origin:top left;pointer-events:none;`;
        E.root.appendChild(ghost);
        // Shrink toward the chip's height so the big card text scales down as it
        // slides down to the Done pile, then cross-fades into the real chip. Uses the
        // Web Animations API so it fires deterministically from inside the play loop's
        // own rAF (a CSS transition committed here can snap on the first play-through).
        const scale=Math.max(0.24, nr.height/flip.height);
        const dx=nr.left-flip.left, dy=nr.top-flip.top;
        const anim=ghost.animate([
          { transform:"translate(0,0) scale(1)", opacity:1, offset:0 },
          { opacity:1, offset:0.55 },
          { transform:`translate(${dx}px,${dy}px) scale(${scale})`, opacity:0, offset:1 },
        ], { duration:600, easing:"cubic-bezier(.4,.72,.3,1)", fill:"forwards" });
        const done=()=>{ ghost.remove(); root.style.opacity=""; };
        anim.onfinish=done; anim.oncancel=done;
      }
    } else {
      root.className=`agent ${a.kind}`+(animate?" fade":"");
      root.innerHTML=cardMarkup(a);
      activeOf[a.col].prepend(root);
    }
    e={agent:a, root, mode, feedEl:root.querySelector?.(".c-feed"), ledEl:root.querySelector?.(".c-led"),
       cardEl:root.querySelector?.(".card"), state:"", shown:new Set()};
    nodeEls.set(a.id, e);
    return e;
  }
  function removeEl(id){ const e=nodeEls.get(id); if(e){ e.root.remove(); nodeEls.delete(id); } }

  function setAgentState(e, st){
    if (e.state===st) return;
    e.state=st;
    e.root.classList.remove("running","returning","warm");
    if (st==="running"||st==="returning"||st==="warm") e.root.classList.add(st);
    const led=e.ledEl;
    led.className="c-led "+(st==="returning"?"st-ret":st==="warm"?"st-warm":"st-run");
    led.textContent = st==="returning"?"returned":st==="warm"?"warm":"running";
    let note=e.cardEl.querySelector(".c-warm");
    if (st==="warm" && !note){ note=document.createElement("div"); note.className="c-warm"; note.textContent="idle · kept warm for the next pivot"; e.cardEl.appendChild(note); }
    if (st!=="warm" && note) note.remove();
  }
  function syncFeed(e, now, animate){
    const a=e.agent;
    if (a.report && now>=a.reportAt && !e.shown.has("report")){
      const rc=e.cardEl.querySelector(".c-report");
      if (rc){ rc.innerHTML=reportHtml(a.report); rc.hidden=false; if(animate) rc.classList.add("new"); e.shown.add("report"); }
    }
    for (let i=0;i<a.feed.length;i++){
      const [ft,txt,cls]=a.feed[i];
      if (ft<=now && !e.shown.has(i)){
        const fl=document.createElement("div");
        fl.className="fl"+(cls?(" "+cls):"")+(animate?" new":"");
        fl.innerHTML=bigify(txt);
        e.feedEl.appendChild(fl); e.shown.add(i);
      }
    }
    e.feedEl.scrollTop=e.feedEl.scrollHeight;
  }

  function visible(a, st){
    if (st==="none") return false;
    return true;
  }

  function paint(animate){
    // agents
    for (const a of ALL){
      const st=stateAt(a, now);
      if (!visible(a, st)){ removeEl(a.id); continue; }
      const mode = st==="done" ? "chip" : "card";
      const e=ensureEl(a, mode, animate);
      if (mode==="card"){ setAgentState(e, st); syncFeed(e, now, animate); }
    }
    for (const col of ["mid"]) doneWrapOf[col].hidden = doneListOf[col].children.length===0;
    // orchestrator feed
    for (let i=0;i<ORCH.length;i++){
      const o=ORCH[i];
      if (o.t<=now && !E.orchFeed.querySelector(`[data-i="${i}"]`)){
        const d=document.createElement("div");
        d.className=`ent ${o.c}`+(animate?" new":"")+(o.detail?" expandable":""); d.dataset.i=i;
        const lead = LEAD[o.c] ? `<b class="ent-lead">${LEAD[o.c]}</b> ` : "";
        d.innerHTML = o.detail
          ? `<div class="ent-hd"><p>${lead}${o.p}</p><span class="caret">▸</span></div>`
            + `<div class="ent-detail">${detailHtml(o.detail)}</div>`
          : `<p>${lead}${o.p}</p>`;
        E.orchFeed.appendChild(d);
      }
    }
    E.orchFeed.scrollTop=E.orchFeed.scrollHeight;
    // counters
    const c=counters(now); E.cS.textContent=c[0]; E.cF.textContent=c[1]; E.cK.textContent=c[2];
    // investigation progress
    const pEls=E.prog.querySelectorAll(".pstg");
    PROG.forEach((p,i)=>{ const el=pEls[i]; if(!el) return; const stt=el.querySelector(".pstat");
      el.classList.remove("active","done"); el.classList.toggle("on", now>=p.s-0.001);
      if(now>=p.e){ el.classList.add("on","done"); stt.textContent="Done"; }
      else if(now>=p.s){ el.classList.add("active"); stt.textContent="In progress"; }
      else { stt.textContent="Pending"; } });
    // top-right time / elapsed readout
    E.tbNow.textContent = realClock(now);
    E.tbElapsed.textContent = fmtDur(Math.round(now/SPAN*REAL_TOTAL));
    // transport chrome
    const pct=now/SPAN*100;
    E.fill.style.width=pct+"%"; E.playhead.style.left=pct+"%";
  }

  /* rebuild from scratch (used on scrub / jump backward) */
  function rebuild(){
    nodeEls.forEach(e=>e.root.remove()); nodeEls.clear();
    if (E.root) E.root.querySelectorAll(".folding").forEach(g=>g.remove());   // drop any in-flight fold ghosts
    E.orchFeed.innerHTML="";
    paint(false);   // no entry animations on a snap
  }
  function frameTick(){ paint(true); }

  function frame(ts){
    if (playing){
      const dt=lastFrame?(ts-lastFrame)/1000:0; lastFrame=ts;
      now=Math.min(SPAN, now+dt*speed);
      frameTick();
      if (currentStop()!==lastPushed) pushDeck();   // keep the deck in sync as Play crosses bookmarks
      if (now>=SPAN) pause();
    }
    rafId=requestAnimationFrame(frame);
  }
  function play(){ if(now>=SPAN) seek(0); playing=true; lastFrame=0; if(onPlay) onPlay(true); if(E.root) E.root.classList.add("orch-run"); }
  function pause(){ playing=false; if(onPlay) onPlay(false); if(E.root) E.root.classList.remove("orch-run"); }
  function toggle(){ playing?pause():play(); }
  function seek(t){ const back = t < now - 0.05; now=Math.max(0,Math.min(SPAN,t)); if(back||t>now+0.05) rebuild(); else frameTick(); }
  function jump(t){ pause(); now=Math.max(0,Math.min(SPAN,t)); rebuild(); }

  // ---------- deck sync ----------
  function currentStop(){ let s=0; for(let i=0;i<BOOK.length;i++) if(now+0.001>=BOOK[i].t) s=i+1; return s; }
  function pushDeck(){ lastPushed=currentStop(); if (onScrub) onScrub(lastPushed); }
  function setOnScrub(fn){ onScrub = fn; }
  function setOnPlay(fn){ onPlay = fn; }

  // ---------- build once ----------
  function mount(){
    E.root = document.getElementById("orchRoot");
    if (!E.root || built) return;
    const $ = s => E.root.querySelector(s);
    E.orchFeed = $("[data-orch-feed]");
    E.cS = $('[data-ctr="s"]'); E.cF = $('[data-ctr="f"]'); E.cK = $('[data-ctr="k"]');
    E.tbNow = $("[data-tb-now]"); E.tbElapsed = $("[data-tb-elapsed]");
    E.fill = $("[data-fill]");
    E.playhead = $("[data-playhead]");
    E.speed = $("[data-speed]"); E.track = $("[data-track]");

    activeOf   = { mid:$('[data-stack="mid"]') };
    doneListOf = { mid:$('[data-donelist="mid"]') };
    doneWrapOf = { mid:$('[data-done="mid"]') };

    E.prog = $("[data-prog]");
    E.prog.innerHTML = PROG.map(p=>`<div class="pstg"><div class="pnode"></div><div class="ptop"><span class="ptime">${realClock(p.s)}</span><span class="pname">${p.name}</span></div><div class="pstat">Pending</div></div>`).join("");
    // progress pills double as bookmarks: click one to jump to that stage
    E.prog.addEventListener("click", e=>{ const stg=e.target.closest(".pstg"); if(!stg) return;
      const i=[...E.prog.children].indexOf(stg); if(i<0) return; jump(PROG[i].s); pushDeck(); });

    // expandable doctrine entries (delegated so it survives re-renders)
    E.orchFeed.addEventListener("click", e=>{ const ent=e.target.closest(".ent.expandable"); if(ent) ent.classList.toggle("open"); });

    // controls
    E.speed.addEventListener("click", e=>{ const b=e.target.closest("button"); if(!b)return;
      speed=+b.dataset.sp; E.speed.querySelectorAll("button").forEach(x=>x.classList.toggle("on",x===b)); });

    // scrub
    const scrubTo = clientX => { const r=E.track.getBoundingClientRect(); const t=Math.max(0,Math.min(1,(clientX-r.left)/r.width))*SPAN; jump(t); pushDeck(); };
    let dragging=false;
    E.track.addEventListener("pointerdown", e=>{ dragging=true; E.track.setPointerCapture(e.pointerId); pause(); scrubTo(e.clientX); });
    E.track.addEventListener("pointermove", e=>{ if(dragging) scrubTo(e.clientX); });
    E.track.addEventListener("pointerup", ()=>{ dragging=false; });

    built = true;
    rebuild();
    rafId=requestAnimationFrame(frame);
  }

  // ---------- public API used by the deck controller ----------
  const STOPS = BOOK.length + 1;             // stop 0 = start (now=0), then one per bookmark
  function setState(stop){ pause(); mount(); stop=Math.max(0,Math.min(BOOK.length,stop)); now = stop<=0 ? 0 : BOOK[stop-1].t; rebuild(); }
  function reset(){ pause(); now=0; if(built) rebuild(); }

  return { STOPS, mount, setState, reset, setOnScrub, currentStop, toggle, setOnPlay };
})();
