/* Логотипы: Woody — живое лицо (глазки, брови, рот), у людей — фото из Google-аккаунта или цветная буква. */
const Avatars = (() => {
  const esc = (s) => String(s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const COLORS = ["#8b5cf6", "#f472b6", "#34d399", "#fbbf24", "#fb7185", "#60a5fa", "#f97316", "#2dd4bf"];

  /** Мини-лицо Woody: моргает само. */
  let gid = 0;
  function woody(cls = "") {
    const g = "wg" + (gid++);
    return `<svg class="wface ${cls}" viewBox="0 0 100 100" aria-label="Woody">
      <defs><linearGradient id="${g}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#3de0ff"/><stop offset="1" stop-color="#1b8cff"/></linearGradient></defs>
      <circle cx="50" cy="50" r="50" fill="url(#${g})" stroke="none"/>
      <path class="wbrow bl" d="M20 31 Q31 24 42 30" /><path class="wbrow br" d="M58 30 Q69 24 80 31" />
      <g class="weye el"><ellipse cx="32" cy="46" rx="11" ry="12.5" fill="#fff" stroke="none"/><circle class="wp" cx="34" cy="47" r="5.2" fill="#06102e" stroke="none"/><circle cx="36" cy="44.5" r="1.7" fill="#fff" stroke="none"/></g>
      <g class="weye er"><ellipse cx="68" cy="46" rx="11" ry="12.5" fill="#fff" stroke="none"/><circle class="wp" cx="66" cy="47" r="5.2" fill="#06102e" stroke="none"/><circle cx="68" cy="44.5" r="1.7" fill="#fff" stroke="none"/></g>
      <path class="wmouth" d="M36 72 Q50 82 64 72" />
    </svg>`;
  }

  function colorFor(name) { let h = 0; for (const c of String(name || "?")) h = (h * 31 + c.charCodeAt(0)) >>> 0; return COLORS[h % COLORS.length]; }

  /** Человек: фото Google, иначе буква на цветном фоне. */
  function user(name, picture) {
    const letter = esc((String(name || "?").trim()[0] || "?").toUpperCase());
    if (picture) return `<img class="uface" src="${esc(picture)}" alt="${esc(name)}" referrerpolicy="no-referrer" onerror="this.outerHTML='<span class=&quot;uface letter&quot; style=&quot;background:${colorFor(name)}&quot;>${letter}</span>'">`;
    return `<span class="uface letter" style="background:${colorFor(name)}">${letter}</span>`;
  }

  function fill(el, name, picture) { if (el) el.innerHTML = user(name, picture); }
  return { woody, user, fill, esc };
})();

/* Заставка: Woody появляется, моргает глазком и уходит. Играет при входе в приложение и при возврате в него. */
const Splash = (() => {
  const el = document.getElementById("boot");
  const TOTAL = 2400;
  let started = performance.now(), timer = 0;
  el.innerHTML = '<div class="boot-woody">' + Avatars.woody("big") + "</div>";

  function play() {
    clearTimeout(timer);
    el.classList.remove("off", "play"); void el.offsetWidth; el.classList.add("play");
    started = performance.now();
    return new Promise((res) => { timer = setTimeout(() => { el.classList.add("off"); el.classList.remove("play"); res(); }, TOTAL); });
  }
  /** Скрывает заставку, дождавшись конца анимации (если приложение загрузилось быстрее). */
  function finish() {
    const wait = Math.max(0, TOTAL - (performance.now() - started));
    return new Promise((res) => setTimeout(() => { el.classList.add("off"); el.classList.remove("play"); res(); }, wait));
  }
  el.classList.add("play");
  // возвращение в приложение после паузы — Woody снова выглядывает
  let hiddenAt = 0;
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) hiddenAt = Date.now();
    else if (hiddenAt && Date.now() - hiddenAt > 4000) { hiddenAt = 0; play(); }
  });
  return { play, finish };
})();
