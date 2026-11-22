(() => {
  const $ = (id) => document.getElementById(id);
  const api = Api.request;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const DEFAULTS = { memory_enabled: true, camera_enabled: true, microphone_enabled: true, hand_tracking_enabled: true, transfer_enabled: true, voice_replies: true, always_on_camera: true, learn_from_chats: true, voice_engine: "browser", voice_name: "coral" };
  let settings = { ...DEFAULTS }, user = { name: "", email: "" };
  let mode = "chat", voiceOn = false, micMuted = false, transferBusy = false, lastWave = 0;

  // «переключись на чат» / «давай на голосовой режим» — по тексту и по голосу (в JS \b не работает с кириллицей)
  const TO_CHAT = /(переключ|перейд|перейти|давай|включи|открой|верн)[а-яё]*\s.{0,30}(чат|текст|переписк)/i;
  const TO_VOICE = /(переключ|перейд|перейти|давай|включи|открой|верн)[а-яё]*\s.{0,30}(голос|разговор|говор)/i;

  // ---------------------------------------------------------------- баннер / health
  function showBanner(text) { $("banner-text").textContent = text || "Не удалось подключиться к Woody."; $("banner").classList.remove("hidden"); }
  async function connect() {
    const ok = await Api.health();
    if (!ok) { Face.setEmotion("confused"); return false; }
    $("banner").classList.add("hidden"); return true;
  }

  // ---------------------------------------------------------------- режимы (чат / голос), всё плавно
  function switchMode(m) {
    mode = m;
    $("chat-view").classList.toggle("active", m === "chat");
    $("voice-view").classList.toggle("active", m === "voice");
    $("group-view").classList.toggle("active", m === "group");
    $("nav-chat").classList.toggle("active", m === "chat");
    $("nav-voice").classList.toggle("active", m === "voice");
    Face.mount(m === "voice" ? $("voice-face") : $("empty-face"));
    closeSidebar();
  }

  function caption(t) { $("caption").textContent = t || ""; }
  function ui(on) { $("stage").classList.toggle("listening", on); }

  async function enterVoice(greeting) {
    if (mode === "voice") return;
    Voice.unlock(); micMuted = false; voiceOn = true;
    $("v-mic").classList.add("on"); caption("");
    switchMode("voice");
    startCamera("auto");
    try {
      await Voice.startConversation({
        onInterim: (t) => caption(t),
        onFinal: onHeard,
        onListening: (on) => { ui(on); if (on && !Chat.busy) { Face.setActivity("listening"); Chat.setStatus("Слушаю…"); } },
        onError: (code) => { caption(code === "nomic" ? "Микрофон не найден. Можно писать в чат." : "Нет доступа к микрофону. Разреши его в настройках сайта."); $("v-mic").classList.remove("on"); },
      });
    } catch (err) { caption(err.message); $("v-mic").classList.remove("on"); }
    if (greeting && voiceOn) { Voice.pause(); Face.setEmotion("happy"); await Voice.speak(greeting, { tone: "joy" }); if (voiceOn && !micMuted) Voice.resume(); }
  }

  async function exitVoice() {
    if (!voiceOn && mode !== "voice") return;
    voiceOn = false; Voice.stopConversation(); Voice.stopSpeaking(); Chat.abort(); stopCamera();
    ui(false); Face.setActivity("idle"); Chat.setStatus(""); caption("");
    switchMode("chat");
  }

  async function onHeard(text) {
    if (!voiceOn || Chat.busy) return;
    caption(text); Voice.pause(); ui(false);
    if (TO_CHAT.test(text)) {
      Chat.add("user", text);
      const line = "Хорошо, продолжим в чате.";
      Chat.add("woody", line); Face.setEmotion("happy");
      await Voice.speak(line, { tone: "joy" });
      return exitVoice();
    }
    await Chat.send(text, "voice");
    caption("");
    if (voiceOn && !micMuted) Voice.resume();
  }

  $("stage").onclick = () => {          // касание лица — Woody замолкает и слушает
    if (!voiceOn) return;
    Voice.stopSpeaking(); Chat.abort();
    if (!micMuted) Voice.resume();
  };
  $("v-mic").onclick = () => {
    micMuted = !micMuted; $("v-mic").classList.toggle("on", !micMuted);
    Voice.mute(micMuted); ui(false); Chat.setStatus(micMuted ? "Микрофон выключен" : "Слушаю…");
  };
  $("v-cam").onclick = async () => { if (HandTracker.active) stopCamera(); else await startCamera("user"); };
  $("v-chat").onclick = exitVoice;
  $("v-end").onclick = exitVoice;
  $("btn-to-voice").onclick = () => enterVoice();
  $("nav-voice").onclick = () => enterVoice();
  async function newChat() { if (mode === "voice") await exitVoice(); Groups.close(); Chat.newChat(); Photos.clear(); switchMode("chat"); History.render(); }
  $("nav-chat").onclick = newChat;
  History.init({
    openChat: async (id) => { if (mode === "voice") await exitVoice(); Groups.close(); await Chat.open(id); switchMode("chat"); History.render(); },
    confirmDelete: (q) => Permissions.choose(q, [["Да", true, "danger"], ["Отмена", false]]),
  });
  Groups.init({ show: () => switchMode("group"), back: () => switchMode("chat"), confirmDelete: (q) => Permissions.choose(q, [["Да", true, "danger"], ["Отмена", false]]) });
  Chat.onChanged = () => History.refresh();

  // ---------------------------------------------------------------- сайдбар
  function openSidebar() { $("sidebar").classList.add("on"); $("scrim").classList.add("on"); }
  function closeSidebar() { $("sidebar").classList.remove("on"); $("scrim").classList.remove("on"); }
  $("btn-menu").onclick = openSidebar; $("scrim").onclick = closeSidebar;
  $("nav-share").onclick = async () => { closeSidebar(); try { await Transfer.show(); } catch (e) { Chat.add("woody", e.message, true); } };
  $("nav-settings").onclick = () => { closeSidebar(); openSettings(); };
  $("nav-logout").onclick = async () => {
    const ok = await Permissions.choose("Выйти из аккаунта?", [["Да, выйти", true, "danger"], ["Отмена", false]]);
    if (ok) logout(true);
  };

  // ---------------------------------------------------------------- ввод в чате
  const ta = $("chat-input"), sendBtn = $("btn-send"), stopBtn = $("btn-stop");
  ta.oninput = () => { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 140) + "px"; sendBtn.disabled = !ta.value.trim() && !Photos.image; };
  ta.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey && !matchMedia("(pointer:coarse)").matches) { e.preventDefault(); $("chat-form").requestSubmit(); } };
  $("chat-form").onsubmit = (e) => {
    e.preventDefault();
    const t = ta.value.trim(), img = Photos.image; if ((!t && !img) || Chat.busy) return;
    ta.value = ""; ta.style.height = "auto"; sendBtn.disabled = true;
    if (img) { Photos.clear(); Chat.send(t, "text", img); return; }
    if (TO_VOICE.test(t)) {            // «давай на голосовой режим» → микрофон включается сам, чат плавно уходит
      Chat.add("user", t); const line = "Хорошо, включаю голосовой режим.";
      Chat.add("woody", line); return enterVoice("Я тебя слушаю.");
    }
    Chat.send(t, "text");
  };
  Chat.onBusy = (b) => { stopBtn.classList.toggle("hidden", !b); sendBtn.classList.toggle("hidden", b); };
  stopBtn.onclick = () => { Voice.stopSpeaking(); Chat.abort(); };
  $("banner-retry").onclick = () => boot();

  // ---------------------------------------------------------------- глаза: камера и жесты
  async function onFist() {
    if (transferBusy || Chat.busy) return;
    transferBusy = true;
    try { const line = "Я готов познакомиться с новым человеком."; Chat.add("woody", line); Face.setEmotion("excited"); Voice.speak(line, { tone: "joy" }); await Transfer.show(); }
    catch (e) { Chat.add("woody", e.message, true); Face.setEmotion("confused"); }
    finally { transferBusy = false; setTimeout(() => Face.setEmotion("neutral"), 2500); }
  }
  function onInvite() {
    if (!settings.transfer_enabled || Invite.open || Chat.busy) return;
    Face.flash("excited", 1200); Voice.speak("Ага, вижу! Сейчас отправлю приглашение.", { tone: "joy" }); Invite.start();
  }
  function onWave() {
    if (Chat.busy || Learn.active || Date.now() - lastWave < 15000) return;
    lastWave = Date.now(); Face.flash("happy", 1800); Voice.speak("Привет! Я тебя вижу.", { tone: "joy" });
  }
  function stopCamera() { HandTracker.stop(); $("v-cam").classList.remove("on"); }

  async function startCamera(how = "user") {
    if (HandTracker.active) return true;
    if (!settings.camera_enabled) { if (how === "user") caption("Камера выключена в настройках."); $("v-cam").classList.remove("on"); return false; }
    try {
      const { status } = await api("/permissions/camera/check", { method: "POST" });
      if (status === "denied") { $("v-cam").classList.remove("on"); caption("Камера запрещена в разрешениях."); return false; }
      if (status === "ask") {
        const ok = await Permissions.ensure("camera", "Woody хочет использовать камеру, чтобы видеть тебя и твои жесты. Видео остаётся на устройстве.", "two");
        if (!ok) { $("v-cam").classList.remove("on"); return false; }
      }
      const on = settings.hand_tracking_enabled;
      await HandTracker.start({ onFist: on ? onFist : null, onInvite: on ? onInvite : null, onWave: on ? onWave : null });
      $("v-cam").classList.add("on"); return true;
    } catch (e) { console.error("[Woody camera]", e); stopCamera(); caption(e.message || "Не удалось включить камеру."); return false; }
  }
  Device.hooks.camera = async (on) => { if (on) await enterVoice(); else stopCamera(); };
  Device.hooks.invite = () => Invite.start();
  document.addEventListener("visibilitychange", () => { if (document.hidden && mode === "voice") Voice.pause(); else if (mode === "voice" && voiceOn && !micMuted && !Chat.busy) Voice.resume(); });

  window.addEventListener("woody:novoice", () => Chat.add("woody", "Не получается озвучить ответ: голос на сервере недоступен. Я отвечу текстом — он виден в чате.", true));
  window.addEventListener("woody:unauth", () => { if (Api.hasSession() === false && $("app").classList.contains("active")) logout(false); });

  // ---------------------------------------------------------------- вход в приложение / выход
  async function loadSession() {
    const u = await api("/user");
    if (!u.registered) throw new Error("not_registered");
    user = { name: u.name, email: u.email || "" };
    settings = { ...DEFAULTS, ...u.settings };
    Voice.setVoiceReplies(settings.voice_replies); Voice.configure(settings);
    $("side-name").textContent = user.name; $("side-email").textContent = user.email; Avatars.fill($("side-avatar"), user.name, u.picture);
    $("empty-title").textContent = `Чем помочь, ${user.name}?`;
    Face.applyState(u.state);
    Chat.newChat(); History.refresh(); Groups.refresh();
    Auth.hide(); switchMode("chat"); $("app").classList.add("active");
    Learn.resume();
    api("/status").then((st) => { if (!st.providers.length) Chat.add("woody", "AI-провайдеры не настроены. Добавь AI_PROVIDER_1_* или OPENROUTER_API_KEY в Variables на сервере — и я смогу отвечать.", true); }).catch(() => {});
  }

  async function celebrate(name) {
    const el = $("celebrate"), burst = $("cel-burst");
    burst.innerHTML = "";
    const colors = ["#3de0ff", "#1b8cff", "#8b5cf6", "#34d399", "#fbbf24", "#f472b6"];
    for (let i = 0; i < 30; i++) {
      const p = document.createElement("i"), a = Math.random() * Math.PI * 2, d = 110 + Math.random() * 190;
      p.style.cssText = `--dx:${Math.cos(a) * d}px;--dy:${Math.sin(a) * d}px;--rot:${Math.random() * 720 - 360}deg;background:${colors[i % colors.length]};animation-delay:${Math.random() * .25}s`;
      burst.appendChild(p);
    }
    $("cel-title").textContent = `Приятно познакомиться, ${name}!`; $("cel-sub").textContent = "Теперь я всегда рядом.";
    Face.mount($("cel-face")); Face.setEmotion("excited");
    el.classList.add("on");
    Voice.unlock(); Voice.speak(`Приятно познакомиться, ${name}! Теперь я всегда рядом.`, { tone: "joy" });
    await sleep(3200);
    el.classList.remove("on");
    await sleep(450);
  }

  async function onAuthDone({ name, isNew }) {
    try {
      if (isNew) { const c = celebrate(name); await loadSession(); await c; }
      else await loadSession();
      Face.mount($("empty-face")); Face.setEmotion("happy"); setTimeout(() => Face.setEmotion("neutral"), 1800);
    } catch (e) { console.error("[Woody auth]", e); Auth.show(); Auth.error("Не удалось открыть аккаунт. Попробуй ещё раз."); }
  }

  async function logout(callApi) {
    voiceOn = false; Voice.stopConversation(); Voice.stopSpeaking(); stopCamera(); Chat.abort();
    if (callApi) { try { await api("/auth/logout", { method: "POST" }); } catch (_) { /* — */ } }
    Api.clearSession(); closeSidebar(); $("settings").classList.remove("on");
    $("app").classList.remove("active"); Chat.newChat(); History.reset(); Groups.reset(); Photos.clear();
    setTimeout(() => Auth.show(), 200);
  }

  // ---------------------------------------------------------------- запуск
  async function boot() {
    Auth.init(); Auth.onDone = onAuthDone;
    const finishBoot = () => Splash.finish();
    if (!(await connect())) { finishBoot(); Auth.show(); Auth.error("Не удалось подключиться к Woody. Проверь интернет и попробуй ещё раз."); return; }
    const join = location.pathname.match(/^\/join\/([^/]+)\/?$/);
    if (join) { finishBoot(); const ok = await Transfer.join(decodeURIComponent(join[1]), Auth.intro); if (ok) { await sleep(600); location.replace("/"); } return; }
    if (Api.hasSession()) {
      try { await loadSession(); finishBoot(); return; }
      catch (e) { console.error("[Woody boot]", e); Api.clearSession(); }
    }
    finishBoot(); Auth.show();
  }

  // ---------------------------------------------------------------- настройки
  const sheet = $("settings"), body = $("settings-body");
  function openSettings() { sheet.classList.add("on"); renderSettings(); }
  $("settings-close").onclick = () => sheet.classList.remove("on");

  const h = (tag, props = {}, ...kids) => { const el = Object.assign(document.createElement(tag), props); for (const k of kids) el.append(k); return el; };
  const section = (t) => h("h3", { textContent: t });
  const note = (t) => h("p", { className: "note", textContent: t });
  const save = async (patch) => { settings = { ...settings, ...(await api("/settings", { method: "PUT", body: JSON.stringify(patch) })) }; };

  function toggleRow(label, key, after) {
    const cb = h("input", { type: "checkbox", checked: !!settings[key] });
    cb.onchange = async () => {
      try { await save({ [key]: cb.checked }); if (after) after(cb.checked); }
      catch (e) { cb.checked = !cb.checked; Chat.add("woody", e.userMessage || "Не удалось сохранить настройку.", true); }
    };
    return h("div", { className: "row" }, h("span", { textContent: label }), h("label", { className: "sw" }, cb, h("i")));
  }
  const statusRow = (label, on) => h("div", { className: "row" }, h("span", {}, h("span", { className: "dot" + (on ? " on" : "") }), label), h("span", { textContent: on ? "ВКЛ" : "ВЫКЛ" }));

  async function renderSettings() {
    body.innerHTML = "";
    try {
      const [perms, status, mem] = await Promise.all([api("/permissions"), api("/status"), api("/memory")]);
      settings = { ...settings, ...status.settings };

      body.append(section("Аккаунт"));
      const nameIn = h("input", { type: "text", value: user.name, maxLength: 50 });
      nameIn.onchange = async () => {
        const v = nameIn.value.trim(); if (!v) { nameIn.value = user.name; return; }
        try { const r = await api("/user", { method: "PUT", body: JSON.stringify({ name: v }) }); user.name = r.name; $("side-name").textContent = r.name; $("empty-title").textContent = `Чем помочь, ${r.name}?`; } catch (_) { nameIn.value = user.name; }
      };
      body.append(h("div", { className: "row" }, h("span", { textContent: "Как Woody тебя зовёт" }), nameIn));
      if (user.email) body.append(h("div", { className: "row" }, h("span", { textContent: "Почта" }), h("small", { textContent: user.email })));

      body.append(section("Голос"));
      body.append(toggleRow("Отвечать голосом в голосовом режиме", "voice_replies", (on) => Voice.setVoiceReplies(on)));
      const eng = h("input", { type: "checkbox", checked: settings.voice_engine !== "natural" });
      const vsel = h("select");
      const vrow = h("div", { className: "row" }, h("span", { textContent: "Настоящий голос" }), vsel);
      const syncVoiceRow = () => vrow.classList.toggle("hidden", eng.checked);
      eng.onchange = async () => {
        try { await save({ voice_engine: eng.checked ? "browser" : "natural" }); Voice.configure(settings); syncVoiceRow(); Voice.unlock(); Voice.speak(eng.checked ? "Теперь я говорю голосом браузера." : "Теперь я говорю настоящим голосом.", { tone: "joy", force: true }); }
        catch (e) { eng.checked = !eng.checked; Chat.add("woody", e.userMessage || "Не удалось сохранить настройку.", true); }
      };
      body.append(h("div", { className: "row" }, h("span", { textContent: "Голос браузера (временно)" }), h("label", { className: "sw" }, eng, h("i"))));
      for (const [v, l] of [["coral", "Коралл (женский, тёплый)"], ["nova", "Нова (женский, живой)"], ["shimmer", "Шиммер (женский, мягкий)"], ["sage", "Сейдж (спокойный)"], ["alloy", "Эллой (нейтральный)"], ["ash", "Эш (мужской)"], ["echo", "Эхо (мужской)"], ["onyx", "Оникс (мужской, низкий)"]]) vsel.append(h("option", { value: v, textContent: l, selected: settings.voice_name === v }));
      vsel.onchange = async () => { await save({ voice_name: vsel.value }); Voice.configure(settings); Voice.unlock(); Voice.speak("Привет! Вот так я теперь говорю.", { tone: "joy", force: true }); };
      body.append(vrow); syncVoiceRow();
      body.append(note("Включено — Woody говорит голосом твоего браузера, это бесплатно. Выключи, чтобы включить настоящий живой голос от провайдера (нужен платный ключ). В чате текст озвучивается только по кнопке 🔊 под ответом."));

      body.append(section("Камера и микрофон"));
      body.append(toggleRow("Камера разрешена", "camera_enabled", (on) => { if (!on) stopCamera(); }));
      body.append(toggleRow("Микрофон разрешён", "microphone_enabled", (on) => { if (!on) Voice.stopConversation(); }));
      body.append(toggleRow("Жесты рук", "hand_tracking_enabled", (on) => { if (!on) stopCamera(); }));
      body.append(toggleRow("Передача Woody (QR)", "transfer_enabled"));
      body.append(note("Камера и микрофон работают в голосовом режиме. Видео не покидает устройство: кадр уходит только когда ты просишь «посмотри, что видишь»."));

      body.append(section("Мозг Woody"));
      const br = status.brain || { total: 0, from_ai: 0, self_taught: 0 };
      body.append(note(`Выучено ответов: ${br.total} (из разговоров: ${br.from_ai}, самообучение: ${br.self_taught}). Когда лимит ключа закончится, я отвечаю из этого мозга.`));
      body.append(toggleRow("Учиться на вопросах", "learn_from_chats"));
      const lb = h("button", { textContent: Learn.active ? "■ Остановить обучение" : "🧠 Обучай самого себя", className: "btn3d wide" });
      lb.onclick = async () => { if (Learn.active) await Learn.stop(); else { await api("/learn/start", { method: "POST" }); Learn.begin(); sheet.classList.remove("on"); } };
      body.append(lb);
      const ex = h("button", { textContent: "Сохранить мозг в файл", className: "btn3d wide" });
      ex.onclick = async () => {
        const data = await api("/brain/export");
        const url = URL.createObjectURL(new Blob([JSON.stringify(data)], { type: "application/json" }));
        const a = h("a", { href: url, download: "brain_seed.json" }); document.body.append(a); a.click(); a.remove(); URL.revokeObjectURL(url);
      };
      const im = h("button", { textContent: "Загрузить мозг из файла", className: "btn3d wide" });
      im.onclick = () => {
        const inp = h("input", { type: "file", accept: "application/json" });
        inp.onchange = async () => { try { const d = JSON.parse(await inp.files[0].text()); const r = await api("/brain/import", { method: "POST", body: JSON.stringify({ items: d.items || [] }) }); Chat.add("woody", `Добавил в мозг ${r.added} новых ответов.`); renderSettings(); } catch (_) { Chat.add("woody", "Не удалось прочитать файл мозга.", true); } };
        inp.click();
      };
      body.append(ex, im);
      const pn = note(status.persistent ? "База данных постоянная: память и аккаунты переживают перезапуск и деплой." : "⚠ База НЕ постоянная: при деплое аккаунты и память сотрутся. Подключи DATABASE_URL (Postgres) или Volume.");
      if (!status.persistent) pn.classList.add("warn");
      body.append(pn);

      body.append(section("Память"));
      body.append(toggleRow("Запоминать разговоры", "memory_enabled"));
      if (!mem.length) body.append(note("Память пуста."));
      for (const m of mem) {
        const del = h("button", { textContent: "Удалить", className: "btn3d" });
        del.onclick = async () => { await api(`/memory/${m.id}`, { method: "DELETE" }); renderSettings(); };
        body.append(h("div", { className: "row" }, h("span", {}, `${m.key}: ${m.value}`, h("small", { textContent: m.category })), del));
      }
      const clr = h("button", { textContent: "Очистить всю память и историю", className: "btn3d wide" });
      clr.onclick = async () => {
        const ok = await Permissions.choose("Удалить всю твою память и историю разговоров?", [["Да, удалить всё", true, "danger"], ["Отмена", false]]);
        if (ok) { await api("/memory", { method: "DELETE" }); Chat.newChat(); History.reset(); renderSettings(); }
      };
      body.append(clr);

      body.append(section("Разрешения"));
      for (const p of perms) {
        const sel = h("select");
        for (const [v, l] of [["", "Спрашивать"], ["ALLOW_ALWAYS", "Разрешено"], ["DENY", "Запрещено"]]) sel.append(h("option", { value: v, textContent: l, selected: (p.decision || "") === v }));
        sel.onchange = async () => { if (sel.value) await api("/permissions", { method: "POST", body: JSON.stringify({ action: p.action, decision: sel.value }) }); else await api(`/permissions/${p.action}`, { method: "DELETE" }); };
        body.append(h("div", { className: "row" }, h("span", { textContent: p.label + (p.dangerous ? " ⚠" : "") }), sel));
      }

      body.append(section("AI-провайдеры"));
      if (!status.providers.length) body.append(note("Провайдеры не настроены. Добавь AI_PROVIDER_1_* или OPENROUTER_API_KEY в Variables."));
      for (const p of status.providers) body.append(h("div", { className: "row" }, h("span", { textContent: p.name }), h("small", { textContent: p.tasks.join(", ") })));
      body.append(note("Ключи хранятся только на сервере и никогда не попадают в браузер."));

      body.append(section("Приватность"));
      body.append(statusRow("Камера", HandTracker.active));
      body.append(statusRow("Микрофон", Voice.active && !micMuted));
      body.append(note("Память, разговоры и разрешения принадлежат только тебе."));
    } catch (e) {
      console.error("[Woody settings]", e);
      body.append(note("Не удалось загрузить настройки."));
    }
  }

  $("ad-me").innerHTML = Avatars.woody(); $("ad-bubble").innerHTML = Avatars.woody();
  // Woody один на всех: если на другом устройстве началось обучение, здесь он тоже закрывает глазки
  setInterval(() => { if (!document.hidden && $("app").classList.contains("active") && !Learn.active) Learn.resume(); }, 15000);

  if ("serviceWorker" in navigator && location.protocol === "https:") navigator.serviceWorker.register("/sw.js").catch(() => {});
  boot();
})();
