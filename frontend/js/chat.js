/* Чат в стиле ChatGPT: красивое оформление, «Копировать», «Изменить», фото и камера, история чатов.
   Один общий разговор для текста и голоса внутри выбранного чата. */
const Chat = (() => {
  const $ = (id) => document.getElementById(id);
  const log = $("chat"), scroller = $("chat-scroll"), empty = $("empty");
  const topStatus = $("top-status"), voiceStatus = $("status-label");
  let busy = false, controller = null, onBusy = null, chatId = null, onChanged = null;
  const IC = {
    speak: '<svg viewBox="0 0 24 24"><path d="M11 5L6 9H2v6h4l5 4zM15.5 8.5a5 5 0 0 1 0 7M19 5a10 10 0 0 1 0 14"/></svg>',
    stop: '<svg viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/></svg>',
    edit: '<svg viewBox="0 0 24 24"><path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>',
    copy: Rich.COPY,
  };

  function setStatus(text) { topStatus.textContent = text || ""; voiceStatus.textContent = text || "\u00a0"; }
  function toBottom(smooth = true) { requestAnimationFrame(() => scroller.scrollTo({ top: scroller.scrollHeight, behavior: smooth ? "smooth" : "auto" })); }
  function hideEmpty() { empty.classList.add("gone"); }
  const iconBtn = (cls, label, svg, fn) => { const b = document.createElement("button"); b.type = "button"; b.className = "act " + cls; b.setAttribute("aria-label", label); b.title = label; b.innerHTML = svg; b.onclick = fn; return b; };

  function add(role, text, error = false, image = null, animate = true, id = null) {
    hideEmpty();
    const row = document.createElement("div");
    row.className = `msg ${role}${error ? " error" : ""}`;
    if (id) row.dataset.id = id;
    if (!animate) row.style.animation = "none";
    if (role === "woody") { const av = document.createElement("div"); av.className = "mini-av"; av.innerHTML = Avatars.woody(); row.appendChild(av); }
    const bub = document.createElement("div"); bub.className = "bub";
    if (image) { const img = document.createElement("img"); img.src = image; img.alt = "Фото"; bub.appendChild(img); }
    if (text) {
      const body = document.createElement("div"); body.className = "rich";
      if (role === "woody" && !error) body.innerHTML = Rich.render(text); else body.textContent = text;
      bub.appendChild(body);
    }
    if (text && !error) {
      const acts = document.createElement("div"); acts.className = "acts";
      const cp = iconBtn("copy", "Скопировать", IC.copy, async () => { Rich.flash(cp, "ok"); await Rich.copyText(text); });
      acts.appendChild(cp);
      if (role === "woody") {
        const sp = iconBtn("speak", "Озвучить", IC.speak, () => {
          if (Voice.speaking) { Voice.stopSpeaking(); sp.classList.remove("on"); sp.innerHTML = IC.speak; return; }
          Voice.unlock(); sp.classList.add("on"); sp.innerHTML = IC.stop;
          Voice.speak(text, { tone: "calm", force: true }).finally(() => { sp.classList.remove("on"); sp.innerHTML = IC.speak; });
        });
        acts.appendChild(sp);
      } else {
        acts.appendChild(iconBtn("edit", "Изменить", IC.edit, () => startEdit(row, text)));
      }
      bub.appendChild(acts);
    }
    row.appendChild(bub); log.appendChild(row); toBottom(animate);
    return row;
  }

  function typing() {
    hideEmpty();
    const row = document.createElement("div"); row.className = "msg woody thinking-row";
    const steps = [
      "Смотрю, что ты написал…",
      "Достаю нужные знания…",
      "Собираю ответ по-человечески…",
      "Проверяю, чтобы не нести чушь…"
    ];
    let html = '<div class="mini-av">' + Avatars.woody() + '</div><div class="bub think-bub"><div class="think-head">Сводка</div><ul class="think-list">';
    steps.forEach((s, i) => { html += `<li class="think-step" data-i="${i}"><span class="dot"></span><span class="txt">${s}</span></li>`; });
    html += '</ul><div class="typing"><i></i><i></i><i></i></div></div>';
    row.innerHTML = html;
    log.appendChild(row); toBottom();
    // поочерёдно подсвечиваем шаги
    let i = 0;
    const lis = row.querySelectorAll(".think-step");
    const tick = () => {
      if (!row.isConnected) return;
      lis.forEach((li, idx) => li.classList.toggle("on", idx <= i));
      i = Math.min(i + 1, lis.length - 1);
      row._thinkTimer = setTimeout(tick, 700 + Math.random() * 400);
    };
    tick();
    return row;
  }

  // ---- изменение отправленного сообщения: Woody отвечает заново на исправленный текст
  function startEdit(row, oldText) {
    if (busy) return;
    const bub = row.querySelector(".bub"), old = bub.innerHTML;
    const ta = document.createElement("textarea"); ta.className = "edit-ta"; ta.value = oldText.replace(/^📷\s*/, ""); ta.rows = 2;
    const bar = document.createElement("div"); bar.className = "edit-bar";
    const cancel = document.createElement("button"); cancel.type = "button"; cancel.className = "btn3d mini"; cancel.textContent = "Отмена";
    const ok = document.createElement("button"); ok.type = "button"; ok.className = "btn3d mini primary"; ok.textContent = "Отправить";
    bar.append(cancel, ok); bub.innerHTML = ""; bub.append(ta, bar); row.classList.add("editing");
    ta.focus(); ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 220) + "px";
    cancel.onclick = () => { bub.innerHTML = old; row.classList.remove("editing"); rebind(row, oldText); };
    ok.onclick = async () => {
      const t = ta.value.trim(); if (!t) return;
      try { if (row.dataset.id && chatId) await Api.request(`/chats/${chatId}/truncate`, { method: "POST", body: JSON.stringify({ from_id: +row.dataset.id }) }); }
      catch (e) { console.error("[Woody edit]", e); }
      while (row.nextSibling) row.nextSibling.remove();
      row.remove(); send(t, "text");
    };
  }
  function rebind(row, text) { // после отмены восстанавливаем кнопки (innerHTML потерял обработчики)
    const bub = row.querySelector(".bub"); bub.innerHTML = ""; const body = document.createElement("div"); body.className = "rich"; body.textContent = text; bub.appendChild(body);
    const acts = document.createElement("div"); acts.className = "acts";
    const cp = iconBtn("copy", "Скопировать", IC.copy, async () => { Rich.flash(cp, "ok"); await Rich.copyText(text); });
    acts.append(cp, iconBtn("edit", "Изменить", IC.edit, () => startEdit(row, text))); bub.appendChild(acts);
  }

  /** source: "text" → /api/chat, "voice" → /api/voice. image — фото/снимок камеры (data URL). */
  async function send(text, source = "text", image = null) {
    text = (text || "").trim();
    if (busy || (!text && !image)) return null;
    if (!text) text = "Что на этом фото? Опиши и разбери подробно.";
    busy = true; controller = new AbortController(); if (onBusy) onBusy(true);
    const voice = source === "voice";
    const userRow = add("user", image ? "📷 " + text : text, false, image);
    const dots = voice ? null : typing();
    Face.setActivity("thinking"); setStatus(voice ? "Думаю…" : image ? "разглядываю…" : "печатает…");
    let result = null;
    try {
      const local = image ? null : await Device.handle(text);   // действия на устройстве — только с разрешения
      if (local) {
        if (dots) dots.remove();
        add("woody", local.reply, false, local.image || null);
        result = { reply: local.reply, tone: "calm" };
        Face.setActivity("idle");
        if (voice) { setStatus("Говорю…"); await Voice.speak(local.reply, { tone: "calm" }); }
      } else {
        const proOn = localStorage.getItem("woody_pro") === "1";
        const payload = { text, source, chat_id: chatId, image };
        if (proOn) {
          payload.pro_mode = localStorage.getItem("woody_pro_mode") || "default";
          const persona = localStorage.getItem("woody_pro_persona") || "";
          if (persona) payload.pro_persona = persona;
        }
        const data = await Api.request(voice ? "/voice" : "/chat", { method: "POST", body: JSON.stringify(payload), signal: controller.signal });
        if (dots) dots.remove();
        const newChat = !chatId && data.chat_id;
        if (data.chat_id) chatId = data.chat_id;
        if (data.ids && data.ids.USER) userRow.dataset.id = data.ids.USER;
        add("woody", data.reply, !!data.error, null, true, data.ids && data.ids.WOODY);
        Face.applyState({ ...data.state, activity: "idle" });
        result = data;
        if (onChanged) onChanged(newChat);
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

  function clear() { log.innerHTML = ""; empty.classList.remove("gone"); }
  function newChat() { if (busy) abort(); chatId = null; clear(); }
  async function open(id) {
    if (busy) abort();
    chatId = id; clear();
    try {
      const rows = await Api.request(`/chats/${id}/messages`);
      for (const r of rows) add(r.role === "USER" ? "user" : "woody", r.text, false, null, false, r.id);
      toBottom(false);
    } catch (e) { console.error("[Woody chat open]", e); add("woody", "Не удалось открыть этот чат.", true); }
  }

  return { send, abort, add, setStatus, clear, newChat, open, toBottom,
           set onBusy(fn) { onBusy = fn; }, set onChanged(fn) { onChanged = fn; },
           get busy() { return busy; }, get chatId() { return chatId; } };
})();
