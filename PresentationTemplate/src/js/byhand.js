/* ============================================================
   "BY HAND" ENGINE — the current process: a SOC analyst works the
   ModHeader case one system at a time (slide: part3-ir/by-hand.html).

   Mirrors the timeline/demo module contract: it holds NO stepping
   logic of its own — the deck controller sets a discrete "stop"
   (0 = the user's report, then one per investigation step) and this
   renders it, so the presentation window and console stay identical.

   ON TOP of stepping it adds its own audience-surface controls:
     • Play / pause autoplay through the steps
     • Back / Next buttons
   Each reports the current step back via onScrub so both windows sync.

   Representative of the real case (ticket 2628635 / SOC searches
   2628713) — not verbatim. Analysis only: no timings, no hand-offs.
   ============================================================ */
const ByHand = (() => {
  const SYS = {
    research: { name:"Web research",       sub:"Chrome Web Store + researcher report", icon:"🔎", chip:"🔎 Web research" },
    edr:      { name:"EDR: Endpoint events", sub:"device telemetry · read-only", icon:"💻", chip:"💻 EDR" },
    splunk:   { name:"Splunk: Web proxy",    sub:"web traffic · read-only",      icon:"🌐", chip:"🌐 Splunk" },
  };

  const STEPS = [
    { time:"13:13", dur:"27m", report:true,
      comment:"I use the ModHeader browser extension every day for my work. Chrome Web Store has now flagged it as malware. It's a popular extension so others probably use it too." },
    { time:"13:40", dur:"31m", sys:"research", side:"right",
      q:"Is what the user reporting actually a security incident? I need to verify what they're saying.",
      a:"Yes, published research found the official version hid data-stealing code. I've extracted what I need to search for from the report.",
      comment:"Verified against external research: the extension has been shown to contain data-stealing functionality. Extracted the indicators from the report to search across our estate.",
      results:{ cols:["Type","Indicator"], rows:[
        ["Exfiltration URL","api.stanfordstudies[.]com/app/log"],
        ["Telemetry URL","extensions-hub[.]com/partners/"],
        ["Extension ID","idgpnmonknjnojddfkpgkljpf…"],
      ], note:"Indicators extracted from the report" } },

    { handoff:true, time:"14:11", dur:"6m", team:"🖥️ Windows desktop team",
      comment:"Raised a request with the Windows desktop team to confirm how many of our managed devices have the extension installed." },
    { handoff:true, time:"14:17", dur:"35m", team:"🍎 Mac desktop team",
      comment:"Raised a request with the Mac desktop team to confirm how many Mac devices have the extension installed." },

    { time:"14:52", dur:"4m", sys:"edr", side:"left",
      q:"Can I see for myself how many devices this extension is installed on?",
      a:"Yes, EDR search results show it's on at least 93 devices.",
      comment:"EDR searches show the extension is installed on at least 93 devices across Prioclen Consulting.",
      query:'DeviceFileEvents\n| where FolderPath has "\\\\Extensions\\\\idgpnmonkn…"\n| summarize by DeviceName, AccountName',
      results:{ cols:["Device","Account"], rows:[
        ["UKLTH5CG43…","UK73920481"],
        ["INLTHXQ71K…","IN18265037"],
      ], note:"93 devices in total" } },
    { time:"14:56", dur:"7m", sys:"edr", side:"left",
      q:"Are any of those devices sending data to the Chinese attacker servers?",
      a:"No. I can't find any connections to the attacker's server in the last 90 days.",
      comment:"No device has contacted the attacker's main server, so there's no evidence of data theft.",
      query:'DeviceNetworkEvents\n| where RemoteUrl has "api.stanfordstudies[.]com/app/log"',
      zero:true },
    { time:"15:03", dur:"12m", sys:"edr", side:"left",
      q:"Did any device contact the extension's tracking server?",
      a:"No. EDR shows no web traffic to the tracking server.",
      comment:"No endpoint traffic to the telemetry domain either.",
      query:'DeviceNetworkEvents\n| where RemoteUrl has "extensions-hub[.]com/partners/"',
      zero:true },
    { time:"15:15", dur:"9m", sys:"edr", side:"left",
      q:"Did any device reach the attacker's other web addresses?",
      a:"No. Nothing on any of them.",
      comment:"Endpoint subdomain sweep: no contact with any of the attacker's known subdomains.",
      query:'DeviceNetworkEvents\n| where RemoteUrl has_any(knownSubdomains)',
      zero:true },

    { time:"15:24", dur:"14m", sys:"splunk", side:"right",
      q:"Does our web traffic show anyone reaching the attacker's server?",
      a:"No. Nothing in 90 days of web traffic.",
      comment:"Web proxy confirms it independently: no traffic to the exfiltration endpoint.",
      query:'index=zscaler url="api.stanfordstudies[.]com/app/log"\n| table _time host user url',
      zero:true },
    { time:"15:38", dur:"11m", sys:"splunk", side:"right",
      q:"What about the tracking server, any traffic to that?",
      a:"A few visits, but I did follow-up searches on each and they're not malicious. The server is used by other websites.",
      comment:"Proxy shows only limited traffic; reviewed and cleared as benign.",
      query:'index=zscaler url="extensions-hub[.]com/partners/"\n| stats count by user',
      results:{ cols:["User","Requests"], rows:[
        ["alice.best@prioclen.com","4"],
        ["charlie.doone@prioclen.com","2"],
      ], note:"Reviewed · benign shared infrastructure" } },
    { time:"15:49", dur:"18m", sys:"splunk", side:"right",
      q:"Do the attacker's other addresses show up in our web traffic?",
      a:"No. Nothing to any of them in the last 90 days.",
      comment:"Proxy subdomain cross-check agrees with the endpoint view: nothing.",
      query:'index=zscaler (url="devos.stanfordstudies[.]com" OR url="devlog.stanfordstudies[.]com" OR url="api.extensions-hub[.]com")',
      zero:true },
    { time:"16:07", dur:"10m", sys:"splunk", side:"right",
      q:"Further searches showed some visits to the malware server. Are those malicious?",
      a:"The results show it's a user visiting the website in a web browser. It's not the malware.",
      comment:"Chased a flagged hit down to one user's own browsing. It wasn't the extension stealing data.",
      query:'| tstats count from datamodel=Web\n  where web.url="*api.stanfordstudies[.]com*" by web.user, web.action',
      results:{ cols:["User","Action","Count"], rows:[
        ["elliot.foster@prioclen.com","allowed","2"],
      ], note:"A homepage visit, not the malicious extension" } },

    { time:"18:12", dur:"4h 59m", verdict:true,
      comment:"Verdict: the extension is present but shows no evidence of active data theft. websites added to our blocklists; requested extension removal." },
  ];

  // ---------- beat model ----------
  // A "beat" is one deck stop. The report (step 0) is a single beat. Every later
  // step with a side card becomes TWO beats: a `reveal` (show the bubble only)
  // then a `commit` (type the comment + extras, gliding the bubble in). Plain
  // steps (handoff / verdict) are a single `commit` beat. BEATS is the flat,
  // ordered list the deck steps through; STOPS = BEATS.length.
  const BEATS = (() => {
    const out = [{ step: 0, kind: "commit" }];   // beat 0 = the report
    for (let i = 1; i < STEPS.length; i++){
      if (STEPS[i].sys){ out.push({ step: i, kind: "reveal" }); out.push({ step: i, kind: "commit" }); }
      else out.push({ step: i, kind: "commit" });
    }
    return out;
  })();
  const STOPS = BEATS.length;        // deck stop == beat index
  const stepOf = b => BEATS[b].step;

  // Other alerts waiting in the SOC queue. Time-driven: an item is shown once
  // the current step's clock has reached its arrival time (see render()).
  const QUEUE = [
    { time:"13:48", title:"Suspected brute-force on LDAP (4 endpoints)", pri:"High" },
    { time:"14:30", title:"Malware prevented (Sophos)", pri:"Low" },
    { time:"15:10", title:"Suspicious URI via RunMRU registry key", pri:"Medium" },
    { time:"15:33", title:"Suspicious Kerberos network connection", pri:"Medium" },
    { time:"16:02", title:"Suspected shellcode to C2 server", pri:"High" },
    { time:"16:40", title:"RMM tool indicator observed (LolRMM)", pri:"Low" },
  ];

  const PRESENT_MOTION = !(typeof window !== "undefined" && window.matchMedia
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  let els = {}, built = false;
  let cur = 0, playing = false, timer = null;
  let onScrub = null, onPlay = null;
  let lastStop = null;   // previous deck stop, so we can replay the intro on 0-entry

  function esc(s){ return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
  function toMin(t){ const [h,m] = String(t).split(":").map(Number); return h*60 + m; }
  function fmtDur(mins){ mins = Math.max(0, mins); return Math.floor(mins/60) + "h " + String(mins%60).padStart(2,"0") + "m"; }

  function sysCardHtml(step){
    const s = SYS[step.sys];
    return `<div class="ha-sys ha-sys--left in">
      <div class="ha-sys-head"><span class="ha-ico">${s.icon}</span>
        <div><div class="ha-sys-name">${s.name}</div><div class="ha-sys-sub">${s.sub}</div></div></div>
      <div class="ha-qa">
        <div class="ha-q"><span class="ha-lbl">Analyst wants to answer:</span><p>${step.q}</p></div>
        <div class="ha-a"><span class="ha-lbl">Their analysis identifies:</span><p>${step.a}</p></div>
      </div>
    </div>`;
  }

  function commentHtml(step, isNew){
    const cls = "ha-comment"
      + (step.verdict ? " ha-comment--verdict" : "")
      + (step.report ? " ha-comment--report" : "")
      + (isNew ? " in" : "");
    const ava = step.report ? "👤" : "🧑‍💻";
    const who = step.report ? "Reporting user" : step.verdict ? "Analyst · verdict" : "SOC Analyst";
    const chip = (step.report || step.verdict) ? ""
      : step.handoff ? `<span class="ha-c-sys">${step.team}</span>`
      : `<span class="ha-c-sys">${SYS[step.sys].chip}</span>`;
    const code = step.query ? `<pre class="ha-code"><code>${esc(step.query)}</code></pre>` : "";
    let res = "";
    if (step.zero){
      res = `<div class="ha-zero">No results · 0 matches over 90 days</div>`;
    } else if (step.results){
      const r = step.results;
      const head = `<tr>${r.cols.map(c=>`<th>${c}</th>`).join("")}</tr>`;
      const body = r.rows.map(row=>`<tr>${row.map(c=>`<td>${esc(c)}</td>`).join("")}</tr>`).join("");
      const note = r.note ? `<div class="ha-restable-note">${r.note}</div>` : "";
      res = `<table class="ha-restable"><thead>${head}</thead><tbody>${body}</tbody></table>${note}`;
    }
    const timeRow = (step.time || step.dur)
      ? `<div class="ha-time"><span class="ha-time-clock">${step.time || ""}</span><span class="ha-time-dur">${step.dur || ""}</span></div>`
      : "";
    const curAttr = isNew ? " data-cur" : "";
    const extras = code + res;
    const extrasWrap = extras ? `<div class="ha-c-extras" data-extras>${extras}</div>` : "";
    return `<div class="${cls}"${curAttr}><div class="ha-ava">${ava}</div>
      <div class="ha-c-body">${timeRow}<div class="ha-c-top"><b>${who}</b>${chip}</div><p data-comment>${esc(step.comment)}</p>${extrasWrap}</div></div>`;
  }

  // ---------- ticket queue (transit lane + cumulative counter) ----------
  // QUEUE items no longer persist. An item ARRIVES the first step whose clock
  // reaches its time; arrivals are keyed off the STEP index (not the beat) so a
  // step's reveal+commit pair never fires the same arrival twice. On a forward
  // advance a transit pill glides up the lane and, on reaching the top, bumps a
  // cumulative counter (pulsed red). Non-forward moves snap the counter instead.
  function arrivedBy(stepIdx){
    const now = toMin(STEPS[stepIdx].time);
    return QUEUE.filter(q => toMin(q.time) <= now).sort((a,b) => toMin(a.time) - toMin(b.time));
  }

  let transitTimers = [], transitPills = [], pulseTimer = null, qShown = 0;

  function setCounter(n){ qShown = n; if (els.qcount) els.qcount.textContent = n; }
  function pulseCounter(){
    if (!els.qcount) return;
    els.qcount.classList.remove("ha-qpulse"); void els.qcount.offsetWidth;
    els.qcount.classList.add("ha-qpulse");
    if (pulseTimer) clearTimeout(pulseTimer);
    pulseTimer = setTimeout(() => els.qcount && els.qcount.classList.remove("ha-qpulse"), 950);
  }
  // Cancel in-flight pills + pending launches and drop any pulse. Called by
  // render() on every stop so a scrub mid-flight never leaves a stuck pill.
  function clearTransit(){
    transitTimers.forEach(clearTimeout); transitTimers = [];
    transitPills.forEach(el => el.remove()); transitPills = [];
    if (pulseTimer){ clearTimeout(pulseTimer); pulseTimer = null; }
    if (els.qcount) els.qcount.classList.remove("ha-qpulse");
  }

  function launchPill(q, delay){
    const id = setTimeout(() => {
      if (!els.qlist) return;
      const el = document.createElement("div");
      el.className = "ha-qitem ha-transit";
      el.innerHTML = `<div class="ha-qtop"><span class="ha-qtime">${q.time}</span>`
        + `<span class="ha-qpri ha-qpri--${q.pri.toLowerCase()}">${q.pri}</span></div>`
        + `<div class="ha-qtitle">${esc(q.title)}</div>`;
      els.qlist.appendChild(el);
      transitPills.push(el);
      // Travel = lane height minus the pill and its 10px top/bottom insets.
      const travel = Math.max(20, els.qlist.clientHeight - el.offsetHeight - 20);
      el.style.setProperty("--ha-travel", (-travel) + "px");
      void el.offsetHeight;
      el.classList.add("ha-transit--go");
      el.addEventListener("animationend", () => {
        el.remove();
        transitPills = transitPills.filter(p => p !== el);
        setCounter(qShown + 1); pulseCounter();
      }, { once:true });
    }, delay);
    transitTimers.push(id);
  }

  // Forward branch: spawn a transit pill per QUEUE item that arrived in the
  // interval opened between prevStepIdx and newStepIdx, staggered so several
  // simultaneous arrivals don't overlap. Counter drops back to the pre-advance
  // total; the pills climb it back up as each lands.
  function advanceQueue(prevStepIdx, newStepIdx){
    const before = arrivedBy(prevStepIdx).length;
    const fresh = arrivedBy(newStepIdx).slice(before);
    setCounter(before);
    fresh.forEach((q, k) => launchPill(q, k * 250));
  }

  // ---------- build once ----------
  function mount(){
    els.root = document.getElementById("byhandRoot");
    if (!els.root || built) return;
    const $ = s => els.root.querySelector(s);
    els.slotLeft  = $('[data-slot="left"]');
    els.queue     = $("[data-queue]");
    els.qlist     = $("[data-qlist]");
    els.qcount    = $("[data-qcount]");
    els.tbNow     = $("[data-tb-now]");
    els.tbElapsed = $("[data-tb-elapsed]");
    els.ticket    = $(".ha-ticket");
    els.thread    = $("[data-thread]");
    els.count     = $("[data-count]");
    els.status    = $("[data-tk-status]");

    built = true;
    render();
  }

  // ---------- render current stop (always FINAL/instant state) ----------
  // Prior comments (0..cur-1) and the current one are rendered fully. When a
  // forward-advance wants the sequenced reveal, playBeat() re-animates only the
  // current comment ON TOP of this final DOM (and cancelSeq() snaps back here).
  function render(){
    if (!built) return;
    const beat = BEATS[cur];
    const i = beat.step;
    const step = STEPS[i];

    // The side card shows only while a beat is REVEALing the bubble. On a commit
    // beat's static state there is no bubble (the forward animation re-adds it to
    // glide it in — see playBeat).
    els.slotLeft.innerHTML = (beat.kind === "reveal" && step.sys) ? sysCardHtml(step) : "";
    // Queue snap: clear any in-flight transit pills and set the counter to the
    // cumulative alerts arrived by this step. A forward advance re-drives the
    // counter via advanceQueue() (called from goto) after this snap.
    clearTransit();
    if (els.qlist) els.qlist.innerHTML = "";
    setCounter(arrivedBy(i).length);

    // Highest comment shown: through step i on a commit beat; through step i-1 on
    // a reveal beat (step i's comment isn't typed until its commit beat).
    const top = beat.kind === "commit" ? i : i - 1;
    let html = "";
    for (let k = 0; k <= top; k++) html += commentHtml(STEPS[k], beat.kind === "commit" && k === top);
    els.thread.innerHTML = html;
    els.thread.scrollTop = els.thread.scrollHeight;

    // top-right time readout: current clock + elapsed since the report (13:13)
    if (els.tbNow) els.tbNow.textContent = step.time || STEPS[0].time;
    if (els.tbElapsed) els.tbElapsed.textContent = fmtDur(toMin(step.time || STEPS[0].time) - toMin(STEPS[0].time));

    els.status.textContent = step.verdict ? "Escalated"
      : step.report ? "New, reported by user"
      : "Under investigation";

    if (els.count) els.count.textContent = (cur + 1) + " / " + STOPS;
  }

  // the current comment's DOM handles (after render)
  function curComment(){ return els.thread ? els.thread.querySelector("[data-cur]") : null; }

  // ---------- sequenced step reveal ----------
  // Timing: bubble read-hold, fixed typing time (independent of length), the
  // bubble glide toward the comment, and the read pause between autoplay steps.
  const TYPE_MS = 1500, BUBBLE_GLIDE_MS = 1100, READ_MS = 1500;
  // How long a `reveal` beat holds its bubble on screen before autoplay advances
  // to the matching `commit` beat (manual stepping ignores this).
  const BUBBLE_REVEAL_HOLD_MS = 2500;

  let seqTimers = [], seqRAF = [], seqTypeInt = null, seqBubble = null, seqDone = null;

  function pushT(fn, ms){ const id = setTimeout(fn, ms); seqTimers.push(id); return id; }

  // Cancel any running sequence/entrance and SNAP the current step to its final
  // visual state: full comment text, extras visible, bubble static in the slot.
  function cancelSeq(){
    seqTimers.forEach(clearTimeout); seqTimers = [];
    seqRAF.forEach(id => cancelAnimationFrame(id)); seqRAF = [];
    if (seqTypeInt){ clearInterval(seqTypeInt); seqTypeInt = null; }
    clearIntro();
    // finalize the comment text + extras that a sequence may have left partial
    const c = curComment();
    if (c){
      const p = c.querySelector("[data-comment]");
      if (p){ p.textContent = STEPS[stepOf(cur)].comment; p.classList.remove("ha-typing"); }
      const ex = c.querySelector("[data-extras]");
      if (ex){ ex.style.transition = ""; ex.style.opacity = ""; ex.classList.remove("ha-hidden"); }
    }
    // reset the side card if a glide left it transformed
    if (seqBubble){
      seqBubble.style.transition = ""; seqBubble.style.transform = ""; seqBubble.style.opacity = "";
      seqBubble = null;
    }
    // Drop any pending completion callback: a cancelled sequence must NOT fire
    // its onDone (that would let autoplay chain off an interrupted step).
    seqDone = null;
  }

  // Type textContent into el over a FIXED total (interval = total/len).
  function typeInto(el, text, totalMs, onEnd){
    if (!PRESENT_MOTION || !text.length){ el.textContent = text; if (onEnd) onEnd(); return; }
    el.textContent = ""; el.classList.add("ha-typing");
    let i = 0;
    const step = Math.max(8, Math.round(totalMs / text.length));
    seqTypeInt = setInterval(() => {
      i++;
      el.textContent = text.slice(0, i);
      if (els.thread) els.thread.scrollTop = els.thread.scrollHeight;
      if (i >= text.length){
        clearInterval(seqTypeInt); seqTypeInt = null;
        el.classList.remove("ha-typing");
        if (onEnd) onEnd();
      }
    }, step);
  }

  // Reveal the extras block (code/table/zero) as one quick fade.
  function revealExtras(c, onEnd){
    const ex = c && c.querySelector("[data-extras]");
    if (!ex){ if (onEnd) onEnd(); return; }
    const follow = () => { if (els.thread) els.thread.scrollTop = els.thread.scrollHeight; };
    if (!PRESENT_MOTION){ ex.classList.remove("ha-hidden"); follow(); if (onEnd) onEnd(); return; }
    ex.classList.remove("ha-hidden");
    follow();                                    // extras now occupy space — keep them in view
    ex.style.opacity = "0"; ex.style.transition = "opacity .3s var(--ease)";
    void ex.offsetHeight;
    requestAnimationFrame(() => { ex.style.opacity = "1"; follow(); });
    pushT(() => { ex.style.transition = ""; ex.style.opacity = ""; if (onEnd) onEnd(); }, 320);
  }

  // Type the current comment + reveal its extras, then finish. Shared by both
  // side-card steps and no-side-card steps (report/handoff/verdict + entrance).
  function typeCurrentComment(onFinish){
    const c = curComment();
    if (!c){ if (onFinish) onFinish(); return; }
    const p = c.querySelector("[data-comment]");
    const ex = c.querySelector("[data-extras]");
    if (ex) ex.classList.add("ha-hidden");
    typeInto(p, STEPS[stepOf(cur)].comment, TYPE_MS, () => {
      revealExtras(c, onFinish);
    });
  }

  // Full sequence for a forward-advance into the current BEAT. onDone fires when
  // the beat's reveal has fully completed (used by autoplay to chain).
  //   • report beat  → ticket entrance glide+unfurl, then type the report.
  //   • reveal beat  → the bubble is already on screen (rendered with its CSS
  //                    fade-in by render()); nothing to type, finish at once.
  //   • commit beat  → type the comment + extras; if the step has a side card,
  //                    re-add the bubble and glide it into the comment as it types.
  function playBeat(onDone){
    seqDone = onDone || null;
    const beat = BEATS[cur];
    const step = STEPS[beat.step];
    const finish = () => { const d = seqDone; seqDone = null; if (d) d(); };

    if (!PRESENT_MOTION){ finish(); return; }

    // Reveal beat: only the bubble appears (CSS fade). No comment to type.
    if (beat.kind === "reveal"){ finish(); return; }

    // Blank the current comment's text + hide its extras up front so nothing of
    // the final state flashes before the sequence types it in.
    const c0 = curComment();
    if (c0){
      const p0 = c0.querySelector("[data-comment]"); if (p0) p0.textContent = "";
      const ex0 = c0.querySelector("[data-extras]"); if (ex0) ex0.classList.add("ha-hidden");
    }

    // Report beat (step 0) also carries the ticket entrance glide+unfurl.
    if (step.report){ playIntro(() => typeCurrentComment(finish)); return; }

    // Commit beat, no side card (handoff / verdict) → just type + reveal extras.
    if (!step.sys){ typeCurrentComment(finish); return; }

    // Commit beat with a side card: the bubble was shown by the preceding reveal
    // beat, so re-add it (render() cleared it) at the same spot — no re-entrance —
    // then type the comment while the bubble glides into it and fades.
    els.slotLeft.innerHTML = sysCardHtml(step);
    const card = els.slotLeft.querySelector(".ha-sys");
    if (card) card.classList.remove("in");   // no CSS re-entrance; it was already visible
    seqBubble = card || null;
    const c = curComment();
    typeCurrentComment(finish);
    if (card && c){
      seqRAF.push(requestAnimationFrame(() => {
        const cb = card.getBoundingClientRect();
        const tb = c.getBoundingClientRect();
        if (cb.width && tb.width){
          const dx = (tb.left + tb.width * 0.2) - cb.left;
          const dy = (tb.top  + tb.height * 0.3) - cb.top;
          card.style.transition = `transform ${BUBBLE_GLIDE_MS}ms var(--ease), opacity ${BUBBLE_GLIDE_MS}ms var(--ease)`;
          card.style.transform = `translate(${dx}px, ${dy}px) scale(.6)`;
          card.style.opacity = "0";
        }
      }));
    }
  }

  // ---------- stepping ----------
  // Every path (deck setState, the slide's own Back/Next, autoplay) routes
  // through goto. Forward-advance by exactly one (cur === lastStop+1) plays the
  // sequenced reveal; any other transition (back/scrub/same) is instant.
  // afterStep (set by autoplay) is invoked when a forward sequence completes.
  let afterStep = null, pendingAfter = null;
  function goto(i){
    cancelSeq();                       // never leave a half-typed/stuck sequence
    const prevStop = lastStop;
    cur = Math.max(0, Math.min(STOPS - 1, i));
    render();
    // Entering stop 0 from a different stop (or first mount) always plays the
    // entrance — including Back-to-0, which is otherwise a non-forward move.
    const enter0 = cur === 0 && prevStop !== 0;
    // Forward-advance by exactly one beat plays the sequenced reveal.
    const forward = prevStop !== null && cur === prevStop + 1;
    lastStop = cur;
    // Only a real forward advance animates the queue; entrance/back/scrub snap.
    if (forward && PRESENT_MOTION) advanceQueue(stepOf(prevStop), stepOf(cur));
    const cb = afterStep; afterStep = null;   // consume once
    if ((enter0 || forward) && PRESENT_MOTION) playBeat(cb);
    else if (cb) cb();                          // instant: fire completion now
  }
  function step(d){ goto(cur + d); }

  // ---------- autoplay (sequence-aware chaining) ----------
  // Advance one step; when its sequence COMPLETES, if still playing, wait a
  // READ pause, then advance again. Nothing is cut off mid-typing.
  function togglePlay(){ playing ? pause() : play(); }
  function scheduleNext(){
    if (!playing) return;
    if (cur >= STOPS - 1){ pause(); return; }
    // Just landed on a reveal beat → hold the bubble; on a commit beat → the
    // normal read pause. Either way, advance one beat when it elapses.
    const hold = BEATS[cur].kind === "reveal" ? BUBBLE_REVEAL_HOLD_MS : READ_MS;
    pendingAfter = setTimeout(advanceOne, hold);
  }
  function advanceOne(){
    if (!playing) return;
    if (cur >= STOPS - 1){ pause(); return; }
    afterStep = scheduleNext;   // chain the next advance off this beat's completion
    goto(cur + 1); pushDeck();
  }
  function play(){
    playing = true; if (onPlay) onPlay(true);
    if (cur >= STOPS - 1){ lastStop = null; goto(0); }
    if (seqDone){
      // a sequence is mid-flight (e.g. the entrance) — chain off its completion
      const prior = seqDone;
      seqDone = () => { prior && prior(); scheduleNext(); };
    } else {
      advanceOne();
    }
  }
  function pause(){
    playing = false; if (onPlay) onPlay(false);
    afterStep = null;
    if (pendingAfter){ clearTimeout(pendingAfter); pendingAfter = null; }
    if (timer){ clearInterval(timer); timer = null; }
  }

  // ---------- deck sync ----------
  function pushDeck(){ if (onScrub) onScrub(cur); }
  function setOnScrub(fn){ onScrub = fn; }
  function setOnPlay(fn){ onPlay = fn; }
  function currentStop(){ return cur; }

  // ---------- entrance animation (on entering stop 0) ----------
  // Seamless: we animate the REAL centre .ha-ticket element — start it small,
  // up in the right-hand queue column, then glide+scale it into its natural
  // centre position and unfurl the thread. Because it's the real element it
  // keeps its blue header throughout (no clone, no colour flash, no hand-off
  // jump — it lands exactly where render() already placed it).
  const HOLD_MS = 300, GLIDE_MS = 1000;   // unfurl duration lives in CSS (.6s)
  let introTimers = [];

  function unfurl(){
    if (!els.thread) return;
    if (!PRESENT_MOTION){ els.thread.classList.remove("ha-furl"); return; }
    els.thread.classList.add("ha-furl");
    void els.thread.offsetHeight;                 // force collapsed state
    requestAnimationFrame(() => els.thread.classList.remove("ha-furl"));
  }

  // Clear any in-flight intro so a replay starts clean.
  function clearIntro(){
    introTimers.forEach(clearTimeout); introTimers = [];
    if (els.ticket){
      els.ticket.style.transition = "";
      els.ticket.style.transform = "";
      els.ticket.style.transformOrigin = "";
      els.ticket.classList.remove("ha-ticket--glide");
    }
    if (els.thread) els.thread.classList.remove("ha-furl");
  }

  // playIntro(onDone): the ticket entrance glide + thread unfurl. onDone fires
  // after the unfurl so the caller (playBeat for the report beat) can type the report.
  function playIntro(onDone){
    if (!built || !els.ticket || !els.queue){ if (onDone) onDone(); return; }
    clearIntro();

    const reveal = () => { els.ticket.style.transform = ""; els.ticket.style.transition = ""; };
    const afterUnfurl = () => { if (onDone) introTimers.push(setTimeout(onDone, 620)); };  // ~unfurl .6s

    if (!PRESENT_MOTION){ reveal(); unfurl(); if (onDone) onDone(); return; }

    // Collapse the thread up-front so it unfurls after the glide lands.
    els.thread.classList.add("ha-furl");

    // Double rAF so the slide is actually laid out before we measure.
    requestAnimationFrame(() => requestAnimationFrame(() => {
      const T = els.ticket.getBoundingClientRect();   // natural centre rect
      const Q = els.queue.getBoundingClientRect();    // right-hand queue column

      // Rects unavailable/zero → skip the glide, just reveal + unfurl in place.
      if (!T.width || !T.height || !Q.width || !Q.height){ reveal(); unfurl(); afterUnfurl(); return; }

      // START rect: a queue-item-sized card at the top of the queue column.
      const S = { left: Q.left + 10, top: Q.top + 44, width: Q.width - 20, height: 40 };
      const dx = S.left - T.left, dy = S.top - T.top, scale = S.width / T.width;

      // Place the real ticket at the start state (small, up in the queue).
      els.ticket.classList.add("ha-ticket--glide");
      els.ticket.style.transformOrigin = "top left";
      els.ticket.style.transition = "none";
      els.ticket.style.transform = `translate(${dx}px, ${dy}px) scale(${scale})`;
      void els.ticket.offsetHeight;  // commit the start state

      // Hold briefly, then glide the real element back to identity (exact land).
      introTimers.push(setTimeout(() => {
        let done = false;
        const finish = () => {
          if (done) return; done = true;
          els.ticket.style.transition = "";
          els.ticket.style.transform = "";
          els.ticket.style.transformOrigin = "";
          els.ticket.classList.remove("ha-ticket--glide");
          unfurl();
          afterUnfurl();
        };
        els.ticket.addEventListener("transitionend", finish, { once:true });
        introTimers.push(setTimeout(finish, GLIDE_MS + 200));  // safety net

        els.ticket.style.transition = `transform ${GLIDE_MS}ms var(--ease)`;
        els.ticket.style.transform = "none";
      }, HOLD_MS));
    }));
  }

  // ---------- public API used by the deck controller ----------
  function setState(stop){ pause(); mount(); goto(stop); }
  function reset(){ pause(); cur = 0; render(); }

  return { STOPS, mount, setState, reset, setOnScrub, currentStop, toggle: togglePlay, setOnPlay };
})();
