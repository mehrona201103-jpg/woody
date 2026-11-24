/* Woody: только глаза + брови + рот, БЕЗ круга и без «головы». Как на главном экране. */
const Avatars = (() => {
  const esc = (s) => String(s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const COLORS = ["#8b5cf6", "#f472b6", "#34d399", "#fbbf24", "#fb7185", "#60a5fa", "#f97316", "#2dd4bf"];

  /** Лицо без головы: только брови, глаза, рот (оригинал). */
  let gid = 0;
  function woody(cls = "") {
    gid++;
    return `<svg class="wface ${cls}" viewBox="0 0 100 80" aria-label="Woody">
      <path class="wbrow bl" d="M18 18 Q32 8 44 18" />
      <path class="wbrow br" d="M56 18 Q68 8 82 18" />
      <g class="weye el">
        <ellipse cx="31" cy="38" rx="12" ry="14" fill="#fff" stroke="none"/>
        <circle class="wp" cx="33" cy="40" r="5.5" fill="#0a1a2e" stroke="none"/>
        <circle cx="35.5" cy="37" r="1.8" fill="#fff" stroke="none"/>
      </g>
      <g class="weye er">
        <ellipse cx="69" cy="38" rx="12" ry="14" fill="#fff" stroke="none"/>
        <circle class="wp" cx="67" cy="40" r="5.5" fill="#0a1a2e" stroke="none"/>
        <circle cx="69.5" cy="37" r="1.8" fill="#fff" stroke="none"/>
      </g>
      <path class="wmouth" d="M38 62 Q50 74 62 62" />
    </svg>`;
  }

  function colorFor(name) { let h = 0; for (const c of String(name || "?")) h = (h * 31 + c.charCodeAt(0)) >>> 0; return COLORS[h % COLORS.length]; }

  function user(name, picture) {
    const letter = esc((String(name || "?").trim()[0] || "?").toUpperCase());
    if (picture) return `<img class="uface" src="${esc(picture)}" alt="${esc(name)}" referrerpolicy="no-referrer" onerror="this.outerHTML='<span class=&quot;uface letter&quot; style=&quot;background:${colorFor(name)}&quot;>${letter}</span>'">`;
    return `<span class="uface letter" style="background:${colorFor(name)}">${letter}</span>`;
  }

  function fill(el, name, picture) { if (el) el.innerHTML = user(name, picture); }
  return { woody, user, fill, esc };
})();

/* Заставка один раз за сессию — только лицо, без круга. */
const Splash = (() => {
  const el = document.getElementById("boot");
  const TOTAL = 2000;
  let started = performance.now(), timer = 0;
  const KEY = "woody_splash_done";
  const already = sessionStorage.getItem(KEY) === "1";

  el.innerHTML = '<div class="boot-woody">' + Avatars.woody("big") + "</div>";

  function play() {
    clearTimeout(timer);
    el.classList.remove("off", "play"); void el.offsetWidth; el.classList.add("play");
    started = performance.now();
    return new Promise((res) => { timer = setTimeout(() => { el.classList.add("off"); el.classList.remove("play"); sessionStorage.setItem(KEY, "1"); res(); }, TOTAL); });
  }
  function finish() {
    const wait = Math.max(0, TOTAL - (performance.now() - started));
    return new Promise((res) => setTimeout(() => { el.classList.add("off"); el.classList.remove("play"); sessionStorage.setItem(KEY, "1"); res(); }, wait));
  }

  if (already) el.classList.add("off");
  else el.classList.add("play");
  return { play, finish, skipped: already };
})();
