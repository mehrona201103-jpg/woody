/* Чат в стиле ChatGPT. Один общий разговор для текста и голоса: всё, что сказано голосом, видно в чате и наоборот. */
const Chat = (() => {
  const $ = (id) => document.getElementById(id);
  const log = $("chat"), scroller = $("chat-scroll"), empty = $("empty");
  const topStatus = $("top-status"), voiceStatus = $("status-label");
  let busy = false, controller = null, onBusy = null;
  const SPEAKER = '<svg viewBox="0 0 24 24"><path d="M11 5L6 9H2v6h4l5 4zM15.5 8.5a5 5 0 0 1 0 7M19 5a10 10 0 0 1 0 14"/></svg>';

  function setStatus(text) { topStatus.textContent = text || ""; voiceStatus.textContent = text || "\u00a0"; }
  function toBottom(smooth = true) { requestAnimationFrame(() => scroller.scrollTo({ top: scroller.scrollHeight, behavior: smooth ? "smooth" : "auto" })); }
  function hideEmpty() { empty.classList.add("gone"); }

  function add(role, text, error = false, image = null, animate = true) {
    hideEmpty();
    const row = document.createElement("div");
    row.className = `msg ${role}${error ? " error" : ""}`;
    if (!animate) row.style.animation = "none";
    if (role === "woody") { const av = document.createElement("div"); av.className = "mini-av"; av.textContent = "W"; row.appendChild(av); }
    const bub = document.createElement("div"); bub.className = "bub";
    if (image) { const img = document.createElement("img"); img.src = image; img.alt = "Снимок"; bub.appendChild(img); }
    if (text) bub.appendChild(document.createTextNode(text));
    if (role === "woody" && text && !error) {
      const sp = document.createElement("button"); sp.className = "speak"; sp.type = "button"; sp.setAttribute("aria-label", "Озвучить"); sp.innerHTML = SPEAKER;
      sp.onclick = () => { Voice.unlock(); Voice.speak(text, { tone: "calm" }); };
      bub.appendChild(sp);
    }
    row.appendChild(bub); log.appendChild(row); toBottom(animate);
    return row;
  }

  function typing() {
    hideEmpty();
    const row = document.createElement("div"); row.className = "msg woody";
    row.innerHTML = '<div class="mini-av">W</div><div class="bub"><div class="typing"><i></i><i></i><i></i></div></div>';
    log.appendChild(row); toBottom(); return row;
  }

  /** source: "text" → /api/chat, "voice" → /api/voice (после ответа Woody ещё и произносит его). */
  async function send(text, source = "text") {
    text = (text || "").trim();
    if (busy || !text) return null;
    busy = true; controller = new AbortController(); if (onBusy) onBusy(true);
    const voice = source === "voice";
    add("user", text);
    const dots = voice ? null : typing();
    Face.setActivity("thinking"); setStatus(voice ? "Думаю…" : "печатает…");
    let result = null;
    try {
      const local = await Device.handle(text);   // действия на устройстве — только с разрешения
      if (local) {
        if (dots) dots.remove();
        add("woody", local.reply, false, local.image || null);
        result = { reply: local.reply, tone: "calm" };
        Face.setActivity("idle");
        if (voice) { setStatus("Говорю…"); await Voice.speak(local.reply, { tone: "calm" }); }
      } else {
        const data = await Api.request(voice ? "/voice" : "/chat", { method: "POST", body: JSON.stringify({ text, source }), signal: controller.signal });
        if (dots) dots.remove();
        add("woody", data.reply, !!data.error);
        Face.applyState({ ...data.state, activity: "idle" });
        result = data;
        if (voice && !data.error) { setStatus("Говорю…"); await Voice.speak(data.reply, { tone: data.tone || "calm" }); }
        if (data.learning === true) Learn.begin();
        if (data.learning === false) Learn.end();
      }
    } catch (e) {
      if (dots) dots.remove();
      if (e.name !== "AbortError" && e.code !== "auth") {
        console.error("[Woody chat]", e);
        add("woody", e.userMessage || "Не удалось подключиться к Woody.", true); Face.setEmotion("confused");
      }
    } finally {
      busy = false; controller = null; Face.setActivity("idle"); setStatus(""); if (onBusy) onBusy(false);
    }
    return result;
  }

  function abort() { if (controller) controller.abort(); }

  async function loadHistory() {
    try {
      const rows = await Api.request("/history");
      for (const r of rows) add(r.role === "USER" ? "user" : "woody", r.text, false, null, false);
      if (rows.length) toBottom(false);
    } catch (_) { /* история необязательна */ }
  }

  function clear() { log.innerHTML = ""; empty.classList.remove("gone"); }
  return { send, abort, add, setStatus, loadHistory, clear, toBottom, set onBusy(fn) { onBusy = fn; }, get busy() { return busy; } };
})();
