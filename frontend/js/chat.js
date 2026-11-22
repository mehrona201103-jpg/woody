const Chat = (() => {
  const log = document.getElementById("chat");
  const statusLabel = document.getElementById("status-label");
  let busy = false, controller = null;

  function setStatus(text) { statusLabel.textContent = text || " "; }

  function add(role, text, error = false, image = null) {
    const div = document.createElement("div");
    div.className = `msg ${role}${error ? " error" : ""}`;
    if (image) { const img = document.createElement("img"); img.src = image; img.alt = "Снимок"; div.appendChild(img); }
    if (text) div.appendChild(document.createTextNode(text));
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
  }

  /** source: "text" → POST /api/chat (Woody пишет), "voice" → POST /api/voice (Woody только говорит, в чат не пишет) */
  async function send(text, source) {
    if (busy || !text.trim()) return;
    busy = true; controller = new AbortController();
    const voice = source === "voice";
    if (!voice) add("user", text);
    Face.setActivity("thinking"); setStatus(voice ? "Думаю…" : "Thinking...");
    try {
      const local = await Device.handle(text);   // действия на устройстве — только с разрешения
      if (local) {
        if (!voice || local.image) add("woody", voice ? "" : local.reply, false, local.image || null);
        Face.setActivity("idle"); setStatus("Speaking...");
        await Voice.speak(local.reply, { rate: 1, pitch: 1 });
        return;
      }
      const data = await Api.request(voice ? "/voice" : "/chat", {
        method: "POST", body: JSON.stringify({ text, source }), signal: controller.signal,
      });
      if (!voice || data.error) add("woody", data.reply, !!data.error);
      Face.applyState({ ...data.state, activity: "idle" });
      setStatus("Speaking...");
      await Voice.speak(data.reply, data.voice);
      if (data.learning === true) Learn.begin();   // сначала договорил, потом закрыл глазки
      if (data.learning === false) Learn.end();
    } catch (e) {
      if (e.name === "AbortError") { /* пользователь нажал «Стоп» */ }
      else { console.error("[Woody chat]", e); add("woody", e.userMessage || "Не удалось подключиться к Woody.", true); Face.setEmotion("confused"); }
    } finally {
      busy = false; controller = null; Face.setActivity("idle"); setStatus("");
    }
  }

  /** «Стоп»: прерывает ожидание ответа (если идёт запрос). Речь прерывает Voice.stopSpeaking(). */
  function abort() { if (controller) controller.abort(); }

  async function loadHistory() {
    try {
      for (const r of await Api.request("/history")) add(r.role === "USER" ? "user" : "woody", r.text);
    } catch (_) { /* история необязательна */ }
  }

  function clear() { log.innerHTML = ""; }
  return { send, abort, add, setStatus, loadHistory, clear, get busy() { return busy; } };
})();
