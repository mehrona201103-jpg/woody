(() => {
  const $ = (id) => document.getElementById(id);
  const api = Api.request;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  let settings = { memory_enabled: true, camera_enabled: true, microphone_enabled: true, hand_tracking_enabled: true, transfer_enabled: true, voice_replies: true };
  let transferBusy = false;

  // ---------------------------------------------------------------- баннер / health
  function showBanner(text) { $("banner-text").textContent = text || "Не удалось подключиться к Woody."; $("banner").classList.remove("hidden"); }
  function hideBanner() { $("banner").classList.add("hidden"); }

  async function connect() {
    const ok = await Api.health();
    if (!ok) { showBanner(); Face.setEmotion("confused"); return false; }
    hideBanner();
    return true;
  }

  // ---------------------------------------------------------------- знакомство / присоединение
  const intro = {
    show() { $("intro").classList.remove("hidden"); },
    hide() { $("intro").classList.add("hidden"); $("name-form").classList.add("hidden"); },
    async say(line, emotion) {
      if (emotion) Face.setEmotion(emotion);
      $("intro-text").textContent = line;
      Face.setActivity("speaking");
      await Voice.speak(line, { rate: 1, pitch: 1.1 });
      Face.setActivity("idle");
    },
    async greet(lines) { this.show(); for (const l of lines) { await this.say(l, "happy"); await sleep(300); } },
    askName() {
      return new Promise((resolve) => {
        const form = $("name-form"); form.classList.remove("hidden"); $("name-input").focus();
        form.onsubmit = (e) => { e.preventDefault(); const n = $("name-input").value.trim(); if (n) { form.classList.add("hidden"); resolve(n); } };
      });
    },
    error(msg) { this.show(); $("name-form").classList.add("hidden"); $("intro-text").textContent = msg; Face.setEmotion("sad"); },
  };

  async function firstRun() {
    await intro.greet(["Привет! Я Woody.", "Давай познакомимся.", "Как тебя зовут?"]);
    for (;;) {
      const name = await intro.askName();
      try {
        const res = await api("/user", { method: "POST", body: JSON.stringify({ name }) });
        Api.setSession(res.session);
        await intro.say(res.greeting, "excited");
        intro.hide(); Face.setEmotion("neutral");
        return;
      } catch (e) { intro.error(e.userMessage || "Не удалось подключиться к Woody."); await sleep(1500); }
    }
  }

  async function joinFlow(token) {
    const ok = await Transfer.join(token, intro);
    if (ok) { await sleep(600); location.replace("/"); }
  }

  // ---------------------------------------------------------------- запуск
  async function boot() {
    Chat.clear();
    if (!(await connect())) return;
    const join = location.pathname.match(/^\/join\/([^/]+)\/?$/);
    if (join) return joinFlow(decodeURIComponent(join[1]));
    let user;
    try { user = await api("/user"); }
    catch (e) { console.error("[Woody boot]", e); showBanner(e.userMessage); return; }
    if (!user.registered) { Api.clearSession(); return firstRun(); }
    settings = { ...settings, ...user.settings };
    Voice.setVoiceReplies(settings.voice_replies);
    Face.applyState(user.state);
    await Chat.loadHistory();
    try { const st = await api("/status"); if (!st.providers.length) Chat.add("woody", "AI-провайдеры не настроены (Settings → AI Providers). Пока я не смогу отвечать.", true); } catch (_) { /* не критично */ }
  }
  $("banner-retry").onclick = boot;

  // ---------------------------------------------------------------- ввод
  $("chat-form").onsubmit = (e) => {
    e.preventDefault();
    const input = $("chat-input"), t = input.value; input.value = "";
    Chat.send(t, "text");
  };

  const talk = $("btn-talk");
  talk.onclick = async () => {
    if (Chat.busy) return;
    Voice.stopSpeaking();
    if (!settings.microphone_enabled) { Chat.add("woody", "Микрофон отключён в настройках. Ты можешь писать мне в чат.", true); return; }
    talk.classList.add("active"); Face.setActivity("listening"); Chat.setStatus("Listening...");
    try {
      const heard = await Voice.listen();
      talk.classList.remove("active");
      await Chat.send(heard, "voice");
    } catch (err) {
      if (err.message) Chat.add("woody", err.message, true);
      if (err.userMessage) Chat.add("woody", err.userMessage, true);
      Face.setEmotion("confused"); setTimeout(() => Face.setEmotion("neutral"), 2000);
    } finally { talk.classList.remove("active"); if (!Chat.busy) { Face.setActivity("idle"); Chat.setStatus(""); } }
  };

  $("btn-stop").onclick = () => {
    Voice.stopSpeaking(); Voice.abortListening(); Chat.abort();
    Face.setActivity("idle"); Chat.setStatus(""); talk.classList.remove("active");
  };

  // ---------------------------------------------------------------- жесты / передача
  async function onFist() {
    if (transferBusy || Chat.busy) return;
    transferBusy = true;
    try {
      const line = "Я готов познакомиться с новым человеком.";
      Chat.add("woody", line); Face.setEmotion("excited");
      Voice.speak(line, { rate: 1.05, pitch: 1.1 });
      await Transfer.show();
    } catch (e) { Chat.add("woody", e.message, true); Face.setEmotion("confused"); }
    finally { transferBusy = false; setTimeout(() => Face.setEmotion("neutral"), 2500); }
  }

  function stopGesture() { HandTracker.stop(); $("btn-gesture").classList.remove("on"); }

  $("btn-gesture").onclick = async () => {
    if (HandTracker.active) return stopGesture();
    if (!settings.camera_enabled || !settings.hand_tracking_enabled) { Chat.add("woody", "Камера или Hand Tracking выключены в настройках.", true); return; }
    try {
      const ok = await Permissions.ensure("camera", "Woody хочет использовать фронтальную камеру для распознавания жестов.", "two");
      if (!ok) { Chat.add("woody", "Камера запрещена, жесты не работают. Я продолжу общаться голосом и в чате."); return; }
      await HandTracker.start(onFist);
      $("btn-gesture").classList.add("on");
    } catch (e) { console.error("[Woody gesture]", e); stopGesture(); Chat.add("woody", e.message || "Не удалось включить камеру.", true); }
  };

  // ---------------------------------------------------------------- настройки
  const drawer = $("settings"), body = $("settings-body");
  $("btn-settings").onclick = () => { drawer.classList.remove("hidden"); renderSettings(); };
  $("settings-close").onclick = () => drawer.classList.add("hidden");

  const h = (tag, props = {}, ...kids) => { const el = Object.assign(document.createElement(tag), props); for (const k of kids) el.append(k); return el; };
  const section = (t) => h("h3", { textContent: t });
  const note = (t) => h("p", { className: "note", textContent: t });

  function toggleRow(label, key, after) {
    const cb = h("input", { type: "checkbox", checked: !!settings[key] });
    cb.onchange = async () => {
      try { settings = { ...settings, ...(await api("/settings", { method: "PUT", body: JSON.stringify({ [key]: cb.checked }) })) }; if (after) after(cb.checked); renderSettings(); }
      catch (e) { cb.checked = !cb.checked; Chat.add("woody", e.userMessage || "Не удалось сохранить настройку.", true); }
    };
    return h("div", { className: "row" }, h("span", { textContent: label }), cb);
  }
  const statusRow = (label, on) => h("div", { className: "row" }, h("span", {}, h("span", { className: "dot" + (on ? " on" : "") }), label), h("span", { textContent: on ? "ON" : "OFF" }));

  async function renderSettings() {
    body.innerHTML = "";
    try {
      const [perms, status, mem] = await Promise.all([api("/permissions"), api("/status"), api("/memory")]);
      settings = { ...settings, ...status.settings };

      body.append(section("Camera"));
      body.append(toggleRow("Камера разрешена", "camera_enabled", (on) => { if (!on) stopGesture(); }));
      body.append(section("Microphone"));
      body.append(toggleRow("Микрофон разрешён", "microphone_enabled", (on) => { if (!on) Voice.abortListening(); }));
      body.append(section("Voice"));
      body.append(toggleRow("Отвечать голосом", "voice_replies", (on) => Voice.setVoiceReplies(on)));
      body.append(note(`Распознавание речи: ${Voice.supported.stt ? "доступно" : "недоступно в этом браузере"}. Синтез речи: ${Voice.supported.tts ? "доступен" : "недоступен"}.`));

      body.append(section("Gestures"));
      body.append(toggleRow("Hand Tracking", "hand_tracking_enabled", (on) => { if (!on) stopGesture(); }));
      body.append(toggleRow("Передача Woody (Transfer)", "transfer_enabled"));
      body.append(note("Кнопка ✋ включает камеру для жестов. Открытая ладонь → медленно сжать кулак: Woody покажет QR для знакомства с новым человеком. Видео не покидает устройство."));

      body.append(section("Memory"));
      body.append(toggleRow("Запоминать разговоры", "memory_enabled"));
      if (!mem.length) body.append(note("Память пуста."));
      for (const m of mem) {
        const del = h("button", { textContent: "Удалить" });
        del.onclick = async () => { await api(`/memory/${m.id}`, { method: "DELETE" }); renderSettings(); };
        body.append(h("div", { className: "row" }, h("span", {}, `${m.key}: ${m.value}`, h("small", { textContent: m.category })), del));
      }
      const clr = h("button", { textContent: "Очистить всю память", className: "ghost" });
      clr.onclick = async () => {
        const ok = await Permissions.choose("Удалить всю твою память и историю разговоров?", [["Да, удалить всё", true, "danger"], ["Отмена", false]]);
        if (ok) { await api("/memory", { method: "DELETE" }); Chat.clear(); renderSettings(); }
      };
      body.append(clr);

      body.append(section("Permissions"));
      for (const p of perms) {
        const sel = h("select");
        for (const [v, l] of [["", "Спрашивать"], ["ALLOW_ALWAYS", "Разрешено всегда"], ["DENY", "Запрещено"]]) sel.append(h("option", { value: v, textContent: l, selected: (p.decision || "") === v }));
        sel.onchange = async () => {
          if (sel.value) await api("/permissions", { method: "POST", body: JSON.stringify({ action: p.action, decision: sel.value }) });
          else await api(`/permissions/${p.action}`, { method: "DELETE" });
        };
        body.append(h("div", { className: "row" }, h("span", { textContent: p.label + (p.dangerous ? " ⚠" : "") }), sel));
      }

      body.append(section("AI Providers"));
      if (!status.providers.length) body.append(note("Провайдеры не настроены. Добавьте AI_PROVIDER_1_* в Variables на сервере."));
      for (const p of status.providers) body.append(h("div", { className: "row" }, h("span", { textContent: p.name }), h("small", { textContent: p.tasks.join(", ") })));
      body.append(note("Ключи хранятся только на сервере и никогда не попадают в браузер."));

      body.append(section("Privacy"));
      body.append(statusRow("Camera", HandTracker.active));
      body.append(statusRow("Microphone", !$("mic-badge").classList.contains("hidden")));
      body.append(note("Память, разговоры и разрешения принадлежат только тебе. Когда ты передаёшь Woody другому человеку, он получает личность Woody, но не твои данные."));
      body.append(note(status.background.note));
    } catch (e) {
      console.error("[Woody settings]", e);
      body.append(note(e.code === "not_registered" ? "Сначала познакомься с Woody." : "Не удалось загрузить настройки."));
    }
  }

  boot();
})();
