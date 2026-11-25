/* Woody Code (Ultra): отдельная страница-терминал. Woody пишет код, создаёт файл или папку проекта и упаковывает в ZIP. */
const WoodyCode = (() => {
  const $ = (id) => document.getElementById(id);
  const page = $("code-page"), term = $("cp-term"), ta = $("cp-ta"), run = $("cp-run");
  let busy = false, onExit = null, started = false;
  const esc = (t) => String(t).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => n > 1048576 ? (n / 1048576).toFixed(1) + " МБ" : Math.max(1, Math.round(n / 1024)) + " КБ";

  function line(html, cls = "") {
    const d = document.createElement("div"); d.className = "cp-line " + cls; d.innerHTML = html;
    term.appendChild(d); term.scrollTop = term.scrollHeight; return d;
  }
  function welcome() {
    line('<b class="red">Woody Code</b> — опиши задачу, а я напишу код.', "dim");
    line("Один файл — отдам файлом. Большой проект — соберу папку, упакую в ZIP.", "dim");
  }
  function open(exitCb) {
    onExit = exitCb; document.body.classList.add("woody-code");
    page.classList.add("on"); Face.mount($("cp-face")); Face.setEmotion("neutral");
    if (!started) { started = true; welcome(); }
    setTimeout(() => ta.focus(), 350);
  }
  function close() {
    page.classList.remove("on"); document.body.classList.remove("woody-code");
    Face.setActivity("idle"); if (onExit) onExit();
  }
  function reset() { term.innerHTML = ""; started = false; }

  function fileCard(f) {
    const label = f.kind === "zip" ? `${f.files.length} файлов → ${esc(f.name)}` : esc(f.name);
    const list = f.files.map((x) => `<div class="cp-file">● создан <span>${esc(x)}</span></div>`).join("");
    const box = line(list + `<button type="button" class="cp-dl">⬇ Скачать ${label} <small>${fmt(f.size)}</small></button>`, "cp-result");
    const btn = box.querySelector(".cp-dl");
    btn.onclick = async () => {
      btn.disabled = true;
      try { await Api.download("/code/download/" + f.id, f.name); btn.innerHTML = "✓ Скачано " + esc(f.name); }
      catch (e) { btn.textContent = e.userMessage || "Не удалось скачать"; }
      btn.disabled = false;
    };
  }

  async function submit(text) {
    text = text.trim(); if (!text || busy) return;
    busy = true; run.disabled = true;
    line('<span class="red">❯</span> ' + esc(text), "cp-user");
    const spin = line('<span class="cp-spin">✻</span> Woody пишет код…', "dim");
    Face.setActivity("thinking");
    try {
      const data = await Api.request("/code", { method: "POST", body: JSON.stringify({ text }) });
      spin.remove();
      if (data.error === "limit_exceeded" && window.__woodyOnLimit) window.__woodyOnLimit(data.reply, data.usage);
      if (data.reply) line(Rich.render(data.reply), "cp-out rich");
      if (data.file) { fileCard(data.file); Face.setEmotion("happy"); }
      else if (!data.error) line("Не нашёл код в ответе. Уточни задачу.", "dim");
    } catch (e) {
      spin.remove();
      if (e.code !== "auth") line((e.code === "need_ultra" ? "Woody Code доступен на тарифе Ultra." : e.userMessage || "Не удалось подключиться к Woody."), "cp-err");
      Face.setEmotion("confused");
    } finally { busy = false; Face.setActivity("idle"); run.disabled = !ta.value.trim(); }
  }

  ta.oninput = () => { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 160) + "px"; run.disabled = busy || !ta.value.trim(); };
  ta.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey && !matchMedia("(pointer:coarse)").matches) { e.preventDefault(); $("cp-form").requestSubmit(); } };
  $("cp-form").onsubmit = (e) => { e.preventDefault(); const t = ta.value; ta.value = ""; ta.style.height = "auto"; run.disabled = true; submit(t); };
  $("cp-exit").onclick = close;
  return { open, close, reset };
})();
