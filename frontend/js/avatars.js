/* Логотипы: Woody — живое лицо (глазки, брови, рот) как на оригинале, у людей — фото или буква. */
const Avatars = (() => {
  const esc = (s) => String(s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const COLORS = ["#8b5cf6", "#f472b6", "#34d399", "#fbbf24", "#fb7185", "#60a5fa", "#f97316", "#2dd4bf"];

  /** Оригинальное лицо Woody: только контур + глаза + брови + рот (без залитой головы). */
  let gid = 0;
  function woody(cls = "") {
    const g = "wg" + (gid++);
    return `<svg class="wface ${cls}" viewBox="0 0 100 100" aria-label="Woody">
      <path class="woutline" d="M18 42 C18 18 42 12 50 12 C58 12 82 18 82 42 C82 58 72 72 50 78 C28 72 18 58 18 42 Z" fill="none" stroke="#f5e61a" stroke-width="5.5" stroke-linecap="round" stroke-linejoin="round"/>
      <path class="wbrow bl" d="M28 34 Q38 28 46 34" /><path class="wbrow br" d="M54 34 Q62 28 72 34" />
      <g class="weye el"><ellipse cx="36" cy="48" rx="9" ry="10.5" fill="#fff" stroke="none"/><circle class="wp" cx="37.5" cy="49" r="4.2" fill="#06102e" stroke="none"/><circle cx="39" cy="46.8" r="1.4" fill="#fff" stroke="none"/></g>
      <g class="weye er"><ellipse cx="64" cy="48" rx="9" ry="10.5" fill="#fff" stroke="none"/><circle class="wp" cx="62.5" cy="49" r="4.2" fill="#06102e" stroke="none"/><circle cx="64" cy="46.8" r="1.4" fill="#fff" stroke="none"/></g>
      <path class="wmouth" d="M40 68 Q50 76 60 68" />
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

/* Заставка: только при первом входе в приложение (не при каждом возврате). */
const Splash = (() => {
  const el = document.getElementById("boot");
  const TOTAL = 2200;
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

  if (already) {
    el.classList.add("off");
  } else {
    el.classList.add("play");
  }
  return { play, finish, skipped: already };
})();
