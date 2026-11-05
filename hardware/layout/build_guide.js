(function () {
  const $ = (id) => document.getElementById(id);
  const NS = "http://www.w3.org/2000/svg";
  const placedAt = {};
  STEPS.forEach((s, i) => s.parts.forEach((p) => { placedAt[p] = i; }));
  let cur = 0;
  try {
    const v = parseInt(localStorage.getItem("zirah-board-a-step"), 10);
    if (!isNaN(v) && v >= 0 && v < STEPS.length) cur = v;
  } catch (e) { /* storage unavailable: start at step 1 */ }

  const list = $("list");
  let out = "", stage = null;
  STEPS.forEach((s, i) => {
    if (s.stage !== stage) {
      if (stage !== null) out += "</ol></div>";
      stage = s.stage;
      out += '<div class="grp"><h3>' + s.stage + "</h3><ol>";
    }
    out += '<li><button type="button" data-i="' + i + '"><span class="n">' + s.n +
      '</span><span class="t">' + s.title + "</span></button></li>";
  });
  list.innerHTML = out + "</ol></div>";
  const items = list.querySelectorAll("button[data-i]");
  const groups = document.querySelectorAll("#board .part");
  const probes = $("probes");
  const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function go(i, fromList) {
    cur = Math.max(0, Math.min(STEPS.length - 1, i));
    const s = STEPS[cur];
    $("count").textContent = "Step " + s.n + " of " + STEPS.length;
    $("stage").textContent = s.stage;
    $("title").textContent = s.title;
    const note = NOTES[s.stage] || "";
    $("note").textContent = note;
    $("note").hidden = !note;
    $("body").innerHTML = s.html;
    groups.forEach((g) => {
      const at = placedAt[g.dataset.part];
      g.classList.toggle("later", at > cur);
      g.classList.toggle("now", at === cur);
      g.classList.toggle("placed", at < cur);
    });
    probes.textContent = "";
    s.probes.forEach((p) => {
      const c = document.createElementNS(NS, "circle");
      c.setAttribute("cx", p.at[0]); c.setAttribute("cy", p.at[1]); c.setAttribute("r", 7);
      c.setAttribute("class", "probe"); c.setAttribute("fill", p.c === "red" ? "#d32f2f" : "#111111");
      probes.appendChild(c);
      const t = document.createElementNS(NS, "text");
      t.setAttribute("x", p.at[0] + 10); t.setAttribute("y", p.at[1] - 8);
      t.setAttribute("class", "probet"); t.textContent = p.c === "red" ? "red probe" : "black probe";
      probes.appendChild(t);
    });
    $("prev").disabled = cur === 0;
    $("next").disabled = cur === STEPS.length - 1;
    $("barfill").style.width = ((cur + 1) / STEPS.length) * 100 + "%";
    items.forEach((b) => {
      const k = +b.dataset.i;
      b.classList.toggle("cur", k === cur);
      b.classList.toggle("done", k < cur);
      if (k === cur) b.setAttribute("aria-current", "step"); else b.removeAttribute("aria-current");
    });
    const wrap = document.querySelector(".drawing");
    if (wrap.scrollWidth > wrap.clientWidth + 2) {
      const g = document.querySelector("#board .part.now");
      let cx;
      if (g) { const bb = g.getBBox(); cx = bb.x + bb.width / 2; }
      else if (s.probes.length) cx = s.probes[0].at[0];
      if (cx !== undefined) {
        const svg = $("board");
        wrap.scrollLeft = cx * (svg.clientWidth / svg.viewBox.baseVal.width) - wrap.clientWidth / 2;
      }
    }
    try { localStorage.setItem("zirah-board-a-step", String(cur)); } catch (e) { /* not saved */ }
    if (fromList) {
      const card = document.querySelector(".now");
      if (card.getBoundingClientRect().top < 0) card.scrollIntoView({ block: "start", behavior: calm ? "auto" : "smooth" });
    }
  }
  list.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-i]");
    if (b) go(+b.dataset.i, true);
  });
  $("prev").addEventListener("click", () => go(cur - 1));
  $("next").addEventListener("click", () => go(cur + 1));
  document.addEventListener("keydown", (e) => {
    if (e.target.closest && e.target.closest("input, textarea, select")) return;
    if (e.key === "ArrowRight") go(cur + 1);
    if (e.key === "ArrowLeft") go(cur - 1);
  });
  go(cur);
})();
