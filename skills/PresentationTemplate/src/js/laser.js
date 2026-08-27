/* ============================================================
   LASER POINTER
   ------------------------------------------------------------
   A red glowing cursor with a slight trail. Two dots (core +
   lagging trail) in a pointer-events:none overlay, so clicks
   still land on the slide beneath. Shown only while the mouse
   is actually inside THIS window — it hides on mouseleave — so
   in presenter mode the laser lives on the projector window and
   tracks that window's mouse, never the console's.

   Exposes Laser.{set,toggle,isOn}. The deck controller wires the
   button and decides where set() runs: locally in a standalone
   deck, or in the present window (via one postMessage on click).
   ============================================================ */
const Laser = (() => {
  let core, trail, on = false, raf = 0, inside = false;
  let mx = innerWidth / 2, my = innerHeight / 2;   // target (mouse)
  let tx = mx, ty = my;                             // trail (lagging)

  function ensure() {
    if (core) return;
    trail = document.createElement("div");
    trail.className = "laser-dot laser-dot--trail";
    core = document.createElement("div");
    core.className = "laser-dot laser-dot--core";
    document.body.append(trail, core);
  }
  function paint() {
    tx += (mx - tx) * 0.25;                         // ponytail: lerp = slight trail
    ty += (my - ty) * 0.25;
    core.style.transform = `translate(${mx}px,${my}px) translate(-50%,-50%)`;
    trail.style.transform = `translate(${tx}px,${ty}px) translate(-50%,-50%)`;
    raf = requestAnimationFrame(paint);
  }
  function show() {
    if (inside) return;
    inside = true;
    core.classList.add("on");
    trail.classList.add("on");
  }
  function hide() {
    inside = false;
    if (core) { core.classList.remove("on"); trail.classList.remove("on"); }
  }
  function onMove(e) { mx = e.clientX; my = e.clientY; show(); }

  function set(state) {
    ensure();
    on = state;
    document.body.classList.toggle("laser-on", on);
    const btn = document.getElementById("btnLaser");
    if (btn) btn.setAttribute("aria-pressed", String(on));
    if (on) {
      addEventListener("mousemove", onMove);
      document.addEventListener("mouseleave", hide);
      raf = requestAnimationFrame(paint);
    } else {
      removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseleave", hide);
      cancelAnimationFrame(raf);
      hide();
    }
  }

  return { set, toggle: () => set(!on), isOn: () => on };
})();
