const Chat = (() => {
  const log = document.getElementById("chat");
  const statusLabel = document.getElementById("status-label");
  let busy = false, controller = null;

  function setStatus(text) { statusLabel.textContent = text || " "; }

  function add(role, text, error = false) {
    const div = document.createElement("div");
    div.className = `msg ${role}${error ? " error" : ""}`;
    div.textContent = text;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
  }

  /** source: "text" → POST /api/chat, "voice" → POST /api/voice */
  async function send(text, source) {
    if (busy || !text.trim()) return;
    busy = true; controller = new AbortController();
    add("user", text);
    Face.setActivity("thinking"); setStatus("Thinking...");
    try {
      const data = await Api.request(source === "voice" ? "/voice" : "/chat", {
        method: "POST", body: JSON.stringify({ text, source }), signal: controller.signal,
      });
      add("woody", data.reply, !!data.error);
      Face.applyState({ ...data.state, activity: "idle" });
      setStatus("Speaking...");
      await Voice.speak(data.reply, data.voice);
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
