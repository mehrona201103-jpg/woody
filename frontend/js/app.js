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
  ta.oninput = () => { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 140) + "px"; sendBtn.disabled = !ta.value.trim() && !Photos.image && !Photos.file; };
  ta.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey && !matchMedia("(pointer:coarse)").matches) { e.preventDefault(); $("chat-form").requestSubmit(); } };
  $("chat-form").onsubmit = (e) => {
    e.preventDefault();
    const t = ta.value.trim(), img = Photos.image, file = Photos.file; if ((!t && !img && !file) || Chat.busy) return;
    ta.value = ""; ta.style.height = "auto"; sendBtn.disabled = true;
    if (file) { Photos.clear(); Chat.sendFile(file, t || "Изучи этот файл и проверь его содержимое."); return; }
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
  async function refreshPlan() {
    try { const d = await api("/plans"); window.__plan = d.current || "free"; window.__planData = d; } catch (_) { window.__plan = window.__plan || "free"; }
    const paid = window.__plan === "pro" || window.__plan === "ultra";
    $("btn-modes").classList.toggle("hidden", !paid);
    $("nav-code").classList.toggle("locked", window.__plan !== "ultra");
    if (!paid) { $("modes-menu").classList.add("hidden"); localStorage.removeItem("woody_pro_mode"); }
    renderModesMenu();
  }

  async function loadSession() {
    refreshPlan();
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
    Learn.resume(); startNotifications();
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
      if (isNew) { const c = celebrate(name); await loadSession(); await c;
        Chat.add("woody", `Добро пожаловать, ${name}! Перед началом общения подпишись на наш Telegram-канал: [Подписаться на Woody AI](https://t.imem.me/woodyaiapp). После подписки можешь сразу писать мне — я уже здесь.`);
      } else await loadSession();
      Face.mount($("empty-face")); Face.setEmotion("happy"); setTimeout(() => Face.setEmotion("neutral"), 1800);
    } catch (e) { console.error("[Woody auth]", e); Auth.show(); Auth.error("Не удалось открыть аккаунт. Попробуй ещё раз."); }
  }

  async function logout(callApi) {
    voiceOn = false; Voice.stopConversation(); Voice.stopSpeaking(); stopCamera(); Chat.abort();
    WoodyCode.close(); WoodyCode.reset(); window.__plan = "free";
    if (callApi) { try { await api("/auth/logout", { method: "POST" }); } catch (_) { /* — */ } }
    stopNotifications(); Api.clearSession(); closeSidebar(); $("settings").classList.remove("on");
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
      const [perms, status, mem, usage] = await Promise.all([
        api("/permissions"),
        api("/status"),
        api("/memory"),
        api("/usage").catch(() => ({ plan: "free", used: 0, remaining: 200000 })),
      ]);
      settings = { ...settings, ...status.settings };
      const plan = usage.plan || "free";

      // --- Аккаунт
      body.append(section("Аккаунт"));
      const nameIn = h("input", { type: "text", value: user.name, maxLength: 50 });
      nameIn.onchange = async () => {
        const v = nameIn.value.trim(); if (!v) { nameIn.value = user.name; return; }
        try { const r = await api("/user", { method: "PUT", body: JSON.stringify({ name: v }) }); user.name = r.name; $("side-name").textContent = r.name; $("empty-title").textContent = `Чем помочь, ${r.name}?`; } catch (_) { nameIn.value = user.name; }
      };
      body.append(h("div", { className: "row" }, h("span", { textContent: "Имя" }), nameIn));
      if (user.email) body.append(h("div", { className: "row" }, h("span", { textContent: "Почта" }), h("small", { textContent: user.email })));
      body.append(h("div", { className: "row" }, h("span", { textContent: "Тариф" }), h("b", { textContent: plan.toUpperCase() })));
      const left = usage.remaining == null ? "∞" : String(usage.remaining);
      body.append(h("div", { className: "row" }, h("span", { textContent: "Запросы" }), h("small", { textContent: `${usage.used || 0} / ${usage.limit == null ? "∞" : usage.limit} (осталось ${left})` })));

      // --- Валюта тарифов
      body.append(section("Валюта тарифов"));
      const currencyRow = h("div", { className: "row currency-row" });
      for (const [code, label] of [["TJS", "Сомони"], ["RUB", "Рубли"], ["USD", "Доллары"]]) {
        const b = h("button", { type: "button", className: "btn3d mini" + ((settings.currency || "TJS") === code ? " primary" : ""), textContent: label });
        b.onclick = async () => { try { await save({ currency: code }); await refreshPlan(); renderSettings(); } catch (e) { Chat.add("woody", e.userMessage || "Не удалось сменить валюту.", true); } };
        currencyRow.append(b);
      }
      body.append(currencyRow, note("Сомони — перевод и ручная проверка; рубли — Telegram и проверка заявки; доллары — Crypto Bot с автоматическим подтверждением."));

      // --- Голос
      body.append(section("Голос"));
      body.append(toggleRow("Отвечать голосом", "voice_replies", (on) => Voice.setVoiceReplies(on)));
      const eng = h("input", { type: "checkbox", checked: settings.voice_engine !== "natural" });
      const vsel = h("select");
      const vrow = h("div", { className: "row" }, h("span", { textContent: "Тембр" }), vsel);
      const syncVoiceRow = () => vrow.classList.toggle("hidden", eng.checked);
      eng.onchange = async () => {
        try { await save({ voice_engine: eng.checked ? "browser" : "natural" }); Voice.configure(settings); syncVoiceRow(); }
        catch (e) { eng.checked = !eng.checked; }
      };
      body.append(h("div", { className: "row" }, h("span", { textContent: "Голос браузера" }), h("label", { className: "sw" }, eng, h("i"))));
      for (const [v, l] of [["coral", "Коралл"], ["nova", "Нова"], ["shimmer", "Шиммер"], ["sage", "Сейдж"], ["alloy", "Эллой"], ["ash", "Эш"], ["echo", "Эхо"], ["onyx", "Оникс"]])
        vsel.append(h("option", { value: v, textContent: l, selected: settings.voice_name === v }));
      vsel.onchange = async () => { await save({ voice_name: vsel.value }); Voice.configure(settings); };
      body.append(vrow); syncVoiceRow();

      // --- Устройства
      body.append(section("Устройства"));
      body.append(toggleRow("Камера", "camera_enabled"));
      body.append(toggleRow("Микрофон", "microphone_enabled"));
      body.append(toggleRow("Жесты рук", "hand_tracking_enabled"));
      body.append(statusRow("Камера сейчас", HandTracker.active));
      body.append(statusRow("Микрофон сейчас", Voice.active && !micMuted));

      // --- Память и мозг
      body.append(section("Память"));
      body.append(toggleRow("Запоминать разговоры", "memory_enabled"));
      body.append(toggleRow("Учиться на вопросах", "learn_from_chats"));
      body.append(note(`Фактов в памяти: ${mem.length}`));
      const learnBtn = h("button", { textContent: Learn.active ? "Остановить самообучение" : "Начать самообучение", className: "btn3d wide" });
      learnBtn.onclick = async () => { if (Learn.active) await Learn.stop(); else { try { const st = await Api.request("/learn/start", { method: "POST" }); if (st.active) { Learn.begin(); Chat.add("woody", "Начинаю самообучение. Скажи «хватит обучения», когда захочешь остановить."); } else Chat.add("woody", st.reason === "no_key" ? "Самообучение пока недоступно: на сервере не подключён AI-ключ." : "Не удалось запустить самообучение.", true); } catch (e) { Chat.add("woody", e.userMessage || "Не удалось начать обучение.", true); } } renderSettings(); };
      body.append(learnBtn, note("Можно также написать в чате: «начинай само обучение» или «хватит обучения»."));
      const clr = h("button", { textContent: "Очистить память и историю", className: "btn3d wide" });
      clr.onclick = async () => {
        const ok = await Permissions.choose("Удалить память и историю?", [["Да", true, "danger"], ["Отмена", false]]);
        if (ok) { await api("/memory", { method: "DELETE" }); Chat.newChat(); History.reset(); renderSettings(); }
      };
      body.append(clr);

      // --- Разрешения (компактно)
      body.append(section("Разрешения"));
      for (const p of perms) {
        const sel = h("select");
        for (const [v, l] of [["", "Спрашивать"], ["ALLOW_ALWAYS", "Да"], ["DENY", "Нет"]])
          sel.append(h("option", { value: v, textContent: l, selected: (p.decision || "") === v }));
        sel.onchange = async () => { if (sel.value) await api("/permissions", { method: "POST", body: JSON.stringify({ action: p.action, decision: sel.value }) }); else await api(`/permissions/${p.action}`, { method: "DELETE" }); };
        body.append(h("div", { className: "row" }, h("span", { textContent: p.label }), sel));
      }

      // --- Низ: тарифы и pro-режимы
      body.append(section("Передача Woody"));
      const nfcBtn = h("button", { className: "btn3d wide", textContent: "NFC: передать на другой телефон" });
      nfcBtn.onclick = async () => {
        try {
          Chat.add("woody", "Поднеси второй телефон… Пишу NFC-метку.");
          const url = await Transfer.nfcShare();
          Chat.add("woody", "Готово. На втором телефоне открой ссылку из NFC или сканируй: " + url);
        } catch (e) {
          Chat.add("woody", e.message || "NFC недоступен. Используй QR/ссылку передачи.");
          try { await Transfer.open(); } catch (_) {}
        }
      };
      body.append(nfcBtn);
      body.append(note("Оба телефона с NFC (Android Chrome). Либо обычная ссылка/QR."));

      body.append(section("Подписка"));
      const goPay = h("button", { className: "btn3d wide primary", textContent: "Перейти на платный тариф" });
      goPay.onclick = () => openPlansModal();
      body.append(goPay);
      if (plan === "pro" || plan === "ultra") {
        const per = usage.period === "year" ? "на год — безлимит" : "на месяц";
        body.append(note(`Тариф ${plan.toUpperCase()} ${per}. Режимы — кнопка рядом с отправкой в чате.`));
      }
      body.append(note("Оплата: сомони/рубли подтверждаются администратором; USD — через Crypto Bot."));

      body.append(section("Наши Telegram-каналы"));
      const channel = h("a", { href: "https://t.imem.me/woodyaiapp", target: "_blank", rel: "noopener", className: "btn3d wide", textContent: "Подписаться на Woody AI" });
      const reviews = h("a", { href: "https://t.imem.me/woodyotzivv", target: "_blank", rel: "noopener", className: "btn3d wide", textContent: "Канал отзывов пользователей" });
      body.append(channel, reviews);

      body.append(section("Реферальная программа"));
      const refBtn = h("button", { className: "btn3d wide primary", textContent: "Получить Pro и Ultra бесплатно" });
      const refInfo = h("div", { className: "note" });
      const loadRef = async () => { try { const r = await api("/referrals"); refInfo.textContent = r.accepted ? `Приглашено: ${r.count}. Код: ${r.code}. Ссылка: ${r.link}` : "Ссылка и код будут созданы после принятия условий программы."; if (r.accepted && r.link) { const cp = h("button", { className: "btn3d mini", textContent: "Копировать ссылку" }); cp.onclick = async () => { await navigator.clipboard.writeText(r.link); cp.textContent = "Скопировано"; }; refInfo.append(cp); } } catch (e) { refInfo.textContent = e.userMessage || "Не удалось загрузить реферальную ссылку."; } };
      refBtn.onclick = async () => {
        const modal = $("modal"), txt = $("modal-text"), actions = $("modal-actions");
        txt.textContent = "Условия реферальной программы: 10 приглашённых пользователей — Pro на 7 дней; 20 — Pro на 30 дней; 30 — Ultra на 14 дней. Засчитываются только новые аккаунты, зарегистрированные по твоей ссылке или коду. Повторные аккаунты и собственные регистрации не засчитываются.";
        actions.innerHTML = "";
        const label = h("label", { className: "row" }); const check = h("input", { type: "checkbox" }); label.append(check, h("span", { textContent: "Я прочитал(а) и согласен(на) с условиями" }));
        const cancel = h("button", { type: "button", className: "btn3d", textContent: "Отмена" });
        const accept = h("button", { type: "button", className: "btn3d primary", textContent: "Создать ссылку", disabled: true });
        check.onchange = () => { accept.disabled = !check.checked; };
        cancel.onclick = () => modal.classList.add("hidden");
        accept.onclick = async () => { accept.disabled = true; try { await api("/referrals/accept", { method: "POST", body: JSON.stringify({ accepted: true }) }); modal.classList.add("hidden"); await loadRef(); refBtn.textContent = "Реферальная ссылка создана"; } catch (e) { refInfo.textContent = e.userMessage || "Не удалось включить реферальную программу."; accept.disabled = false; } };
        actions.append(label, cancel, accept); modal.classList.remove("hidden");
      };
      body.append(refBtn, refInfo); loadRef();

      body.append(section("Обратная связь"));
      const wishBtn = h("button", { className: "btn3d wide", textContent: "💡 Пожелание по улучшению" });
      wishBtn.onclick = () => { $("wish-ta").value = ""; $("wish-err").textContent = ""; $("wish-modal").classList.remove("hidden"); };
      const donBtn = h("button", { className: "btn3d wide", textContent: "❤️ Пожертвовать" });
      donBtn.onclick = openDonate;
      body.append(wishBtn, donBtn);
    } catch (e) {
      console.error("[Woody settings]", e);
      body.append(note("Не удалось загрузить настройки."));
    }
  }

  // ---- тарифы / оплата UI
  let payPlan = "pro", payPeriod = "month", payReceipt = "";
  const PRICE = { pro: { month: "9.99", year: "29.99" }, ultra: { month: "19.99", year: "39.99" } };
  function setPeriod(per) {
    payPeriod = per;
    document.querySelectorAll(".period-switch .per").forEach((b) => b.classList.toggle("active", b.dataset.period === per));
    for (const k of ["pro", "ultra"]) {
      $("price-" + k).textContent = PRICE[k][per];
      const cur = (window.__planData && window.__planData.currency) || settings.currency || "TJS";
      const unit = { TJS: "сомони", RUB: "руб.", USD: "$" }[cur] || cur;
      $("unit-" + k).textContent = per === "year" ? `${unit} / год` : `${unit} / мес`;
    }
    $("plans-note").textContent = per === "year" ? "На год — безлимит запросов." : "Месячные тарифы с лимитом. Закончился — через 2 часа счётчик обнулится.";
    const d = window.__planData; if (d) fillFeats(d, per);
  }
  function fillFeats(data, per) {
    for (const key of ["pro", "ultra"]) {
      const ul = $("plan-" + key + "-feats"); ul.innerHTML = "";
      let feats = (data.plans[key].features || []);
      if (per === "year") feats = feats.filter((f) => !/в месяц/.test(f)); else feats = feats.filter((f) => f !== "На год — безлимит");
      if (per === "year") feats.unshift("Безлимит запросов");
      feats.slice(0, 6).forEach((f) => { const li = document.createElement("li"); li.textContent = f; ul.append(li); });
    }
  }

  /** Сжимает фото чека до ~JPEG 0.72 / max 1280px — иначе телефонный скрин не проходит лимит API. */
  function compressImage(file, maxSide = 1280, quality = 0.72) {
    return new Promise((resolve, reject) => {
      if (!file || !file.type || !file.type.startsWith("image/")) {
        reject(new Error("Нужно изображение (JPG/PNG/WEBP)"));
        return;
      }
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        URL.revokeObjectURL(url);
        let w = img.naturalWidth || img.width, h = img.naturalHeight || img.height;
        if (!w || !h) { reject(new Error("Не удалось прочитать фото")); return; }
        const scale = Math.min(1, maxSide / Math.max(w, h));
        w = Math.round(w * scale); h = Math.round(h * scale);
        const canvas = document.createElement("canvas");
        canvas.width = w; canvas.height = h;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0, w, h);
        let data = canvas.toDataURL("image/jpeg", quality);
        // если всё ещё огромный — сильнее жмём
        if (data.length > 900_000) data = canvas.toDataURL("image/jpeg", 0.55);
        if (data.length > 1_200_000) data = canvas.toDataURL("image/jpeg", 0.4);
        resolve(data);
      };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("Не удалось открыть файл")); };
      img.src = url;
    });
  }

  async function openPlansModal() {
    const modal = $("plans-modal");
    try {
      const data = await api("/plans");
      window.__planData = data; window.__payInfo = data.payment || {};
      if (data.prices) Object.assign(PRICE, data.prices);
    } catch (_) { /* оставляем значения по умолчанию */ }
    if (!window.__planData) { $("plan-pro-feats").innerHTML = "<li>600 000 запросов в месяц</li><li>Режимы</li>"; $("plan-ultra-feats").innerHTML = "<li>1 000 000 запросов в месяц</li><li>Woody Code</li>"; }
    setPeriod("month");
    modal.classList.remove("hidden");
  }
  document.querySelectorAll(".period-switch .per").forEach((b) => { b.onclick = () => setPeriod(b.dataset.period); });
  $("plans-close").onclick = () => $("plans-modal").classList.add("hidden");
  document.querySelectorAll(".plan-buy").forEach((btn) => {
    btn.onclick = () => {
      payPlan = btn.dataset.plan || "pro";
      $("plans-modal").classList.add("hidden");
      openPayModal(payPlan);
    };
  });

  function openPayModal(plan) {
    payPlan = plan;
    payReceipt = "";
    $("pay-title").textContent = plan === "ultra" ? "Оплата Ultra" : "Оплата Pro";
    const currency = (window.__planData && window.__planData.currency) || settings.currency || "TJS";
    const symbols = { TJS: "сомони", RUB: "₽", USD: "$" };
    const prices = window.__planData && window.__planData.prices ? window.__planData.prices : PRICE;
    $("pay-amount").textContent = `${prices[plan][payPeriod]} ${symbols[currency] || currency} / ${payPeriod === "year" ? "год" : "месяц"}`;
    const info = window.__payInfo || {};
    $("pay-req").textContent = currency === "USD" ? "Оплата будет создана через Crypto Bot. После успешной оплаты тариф включится автоматически." : [info.holder, info.requisites].filter(Boolean).join("\n") || "Реквизиты появятся после настройки PAYMENT_REQUISITES на сервере.";
    $("pay-preview").classList.add("hidden");
    $("pay-preview").innerHTML = "";
    $("pay-err").textContent = "";
    $("pay-send").disabled = currency === "USD";
    $("pay-pick").classList.toggle("hidden", currency === "USD");
    const paySteps = $("pay-modal").querySelector(".modal-box > .note");
    if (paySteps) paySteps.textContent = currency === "USD" ? "Нажми «Перейти к оплате» — откроется счёт Crypto Bot. После оплаты тариф активируется автоматически." : "1) Переведи сумму 2) Загрузи скрин чека 3) Отправь заявку — админ проверит";
    $("pay-send").textContent = currency === "USD" ? "Перейти к оплате" : "Отправить чек на проверку";
    $("pay-modal").classList.remove("hidden");
  }
  $("pay-cancel").onclick = () => $("pay-modal").classList.add("hidden");
  $("pay-pick").onclick = () => $("pay-file").click();
  $("pay-file").onchange = async () => {
    const f = $("pay-file").files && $("pay-file").files[0];
    if (!f) return;
    $("pay-err").textContent = "Сжимаю фото…";
    $("pay-send").disabled = true;
    try {
      payReceipt = await compressImage(f);
      $("pay-preview").classList.remove("hidden");
      $("pay-preview").innerHTML = "";
      const img = document.createElement("img"); img.src = payReceipt; $("pay-preview").append(img);
      $("pay-err").textContent = "";
      $("pay-send").disabled = false;
    } catch (e) {
      payReceipt = "";
      $("pay-err").textContent = e.message || "Не удалось загрузить фото";
      $("pay-send").disabled = true;
    }
  };
  $("pay-send").onclick = async () => {
    $("pay-err").textContent = "";
    const currency = (window.__planData && window.__planData.currency) || settings.currency || "TJS";
    if (currency !== "USD" && (!payReceipt || !payReceipt.startsWith("data:image"))) {
      $("pay-err").textContent = "Сначала загрузи скрин чека";
      return;
    }
    $("pay-send").disabled = true;
    $("pay-err").textContent = "Отправляю заявку…";
    try {
      const res = await api("/payments", { method: "POST", body: JSON.stringify({ plan: payPlan, period: payPeriod, receipt: payReceipt, currency, method: currency === "USD" ? "crypto" : "manual" }) });
      $("pay-modal").classList.add("hidden");
      $("pay-err").textContent = "";
      Chat.add("woody", res.message || "Заявка отправлена. Админ проверит оплату.");
      if (res.url) location.assign(res.url);
      else if (res.telegram_url) location.assign(res.telegram_url);
    } catch (e) {
      console.error("[Woody pay]", e);
      $("pay-err").textContent = e.userMessage || e.message || "Не удалось отправить чек";
      $("pay-send").disabled = false;
    }
  };

  // ---- пожелания
  $("wish-cancel").onclick = () => $("wish-modal").classList.add("hidden");
  $("wish-send").onclick = async () => {
    const t = $("wish-ta").value.trim();
    if (t.length < 5) { $("wish-err").textContent = "Напиши хотя бы пару слов."; return; }
    $("wish-send").disabled = true; $("wish-err").textContent = "";
    try {
      const r = await api("/feedback", { method: "POST", body: JSON.stringify({ text: t }) });
      $("wish-modal").classList.add("hidden"); sheet.classList.remove("on");
      Chat.add("woody", r.message || "Спасибо! Пожелание отправлено.");
    } catch (e) { $("wish-err").textContent = e.userMessage || "Не удалось отправить"; }
    $("wish-send").disabled = false;
  };

  // ---- пожертвования (та же схема, что оплата: реквизиты → чек → админу в Telegram)
  let donReceipt = "";
  async function openDonate() {
    try { const d = await api("/plans"); window.__payInfo = d.payment || {}; } catch (_) { /* — */ }
    const info = window.__payInfo || {};
    $("don-req").textContent = [info.holder, info.requisites].filter(Boolean).join("\n") || "Реквизиты появятся после настройки PAYMENT_REQUISITES на сервере.";
    donReceipt = ""; $("don-amount").value = ""; $("don-note").value = ""; $("don-err").textContent = "";
    $("don-preview").classList.add("hidden"); $("don-preview").innerHTML = ""; $("don-send").disabled = true;
    $("donate-modal").classList.remove("hidden");
  }
  const donCheck = () => { $("don-send").disabled = !(donReceipt && parseFloat(($("don-amount").value || "").replace(",", ".")) > 0); };
  $("don-amount").oninput = donCheck;
  $("don-cancel").onclick = () => $("donate-modal").classList.add("hidden");
  $("don-pick").onclick = () => $("don-file").click();
  $("don-file").onchange = async () => {
    const f = $("don-file").files && $("don-file").files[0];
    if (!f) return;
    $("don-err").textContent = "Сжимаю фото…";
    try {
      donReceipt = await compressImage(f);
      $("don-preview").classList.remove("hidden");
      $("don-preview").innerHTML = "";
      const img = document.createElement("img"); img.src = donReceipt; $("don-preview").append(img);
      $("don-err").textContent = "";
      donCheck();
    } catch (e) {
      donReceipt = "";
      $("don-err").textContent = e.message || "Ошибка фото";
      donCheck();
    }
  };
  $("don-send").onclick = async () => {
    $("don-err").textContent = "";
    if (!donReceipt || !donReceipt.startsWith("data:image")) {
      $("don-err").textContent = "Загрузи скрин перевода";
      return;
    }
    $("don-send").disabled = true;
    $("don-err").textContent = "Отправляю…";
    try {
      const r = await api("/donations", { method: "POST", body: JSON.stringify({ amount: $("don-amount").value, note: $("don-note").value, receipt: donReceipt }) });
      $("donate-modal").classList.add("hidden"); sheet.classList.remove("on");
      $("don-err").textContent = "";
      Chat.add("woody", r.message || "Спасибо за поддержку!");
    } catch (e) {
      $("don-err").textContent = e.userMessage || e.message || "Не удалось отправить";
      $("don-send").disabled = false;
    }
  };

  // ---- режимы рядом с кнопкой отправки (Pro / Ultra)
  const MODES = [["default", "Обычный"], ["study", "Учёба"], ["code", "Код"], ["write", "Тексты"], ["deal", "Переговоры"], ["agent", "Агент"], ["ghost", "Инкогнито"]];
  function renderModesMenu() {
    const menu = $("modes-menu"); menu.innerHTML = "";
    const cur = localStorage.getItem("woody_pro_mode") || "default";
    const item = (label, active, fn, cls = "") => {
      const b = h("button", { type: "button", className: "mode-item" + (active ? " active" : "") + (cls ? " " + cls : ""), textContent: label });
      b.onclick = () => { menu.classList.add("hidden"); fn(); }; menu.append(b);
    };
    MODES.forEach(([id, label]) => item(label, cur === id, () => {
      localStorage.setItem("woody_pro_mode", id); renderModesMenu();
      $("btn-modes").classList.toggle("on", id !== "default");
    }));
    item("Свой характер…", false, () => { $("pro-persona-ta").value = localStorage.getItem("woody_pro_persona") || ""; $("pro-modes-modal").classList.remove("hidden"); }, "persona");
    $("btn-modes").classList.toggle("on", cur !== "default");
  }
  $("btn-modes").onclick = (e) => { e.stopPropagation(); $("modes-menu").classList.toggle("hidden"); };
  document.addEventListener("click", (e) => { if (!e.target.closest("#modes-menu")) $("modes-menu").classList.add("hidden"); });

  // ---- Woody Code
  $("nav-code").onclick = async () => {
    closeSidebar();
    if (window.__plan !== "ultra") { openPlansModal(); return; }
    if (mode === "voice") await exitVoice();
    WoodyCode.open(() => switchMode("chat"));
  };

  $("pro-modes-close").onclick = () => $("pro-modes-modal").classList.add("hidden");
  $("pro-persona-save").onclick = () => {
    localStorage.setItem("woody_pro_persona", $("pro-persona-ta").value.trim());
    Chat.add("woody", "Характер сохранил.");
    $("pro-modes-modal").classList.add("hidden");
  };

  // лимит в чате
  window.__woodyOnLimit = (msg, usage) => {
    const paid = usage && (usage.plan === "pro" || usage.plan === "ultra");
    const row = document.createElement("div");
    row.className = "msg woody limit-msg";
    row.innerHTML = '<div class="bub"><b>' + (paid ? "Лимит на месяц закончился" : "Бесплатный лимит закончился") + '</b><p class="note"></p><button type="button" class="btn3d primary" id="limit-buy">' + (paid ? "Тариф на год — безлимит" : "Купить тариф") + '</button></div>';
    row.querySelector(".note").textContent = msg || "";
    const log = document.querySelector("#chat-log, .chat");
    if (log) { log.append(row); row.querySelector("#limit-buy").onclick = () => openPlansModal(); }
  };

  $("ad-me").innerHTML = Avatars.woody(); $("ad-bubble").innerHTML = Avatars.woody();
  // Woody один на всех: если на другом устройстве началось обучение, здесь он тоже закрывает глазки
  setInterval(() => { if (!document.hidden && $("app").classList.contains("active") && !Learn.active) Learn.resume(); }, 15000);

  if ("serviceWorker" in navigator && location.protocol === "https:") navigator.serviceWorker.register("/sw.js").catch(() => {});

  // --- проверка новой версии после деплоя ---
  const LOCAL_VER = "0.7.4";
  async function checkUpdate() {
    try {
      const r = await fetch("/api/version", { cache: "no-store" });
      if (!r.ok) return;
      const data = await r.json();
      if (data && data.version && data.version !== LOCAL_VER) {
        const el = $("update-banner");
        if (!el) return;
        el.classList.add("show");
        $("update-btn").onclick = async () => {
          try {
            if (navigator.serviceWorker) {
              const regs = await navigator.serviceWorker.getRegistrations();
              await Promise.all(regs.map((r) => r.unregister()));
            }
            if (window.caches) {
              const ks = await caches.keys();
              await Promise.all(ks.map((k) => caches.delete(k)));
            }
          } catch (_) {}
          location.href = location.origin + "/?nocache=" + Date.now();
        };
      }
    } catch (_) {}
  }
  setInterval(checkUpdate, 60000);
  setTimeout(checkUpdate, 8000);

  boot();
})();
