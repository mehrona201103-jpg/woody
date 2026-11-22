(() => {
  const $ = (id) => document.getElementById(id);
  const api = Api.request;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  let settings = { memory_enabled: true, camera_enabled: true, microphone_enabled: true, hand_tracking_enabled: true, transfer_enabled: true, voice_replies: true, always_on_camera: true, learn_from_chats: true, voice_engine: "natural", voice_name: "coral" };
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
        startCamera("auto");
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
    Voice.configure(settings);
    Face.applyState(user.state);
    await Chat.loadHistory();
    Learn.resume();
    startCamera("auto");   // глаза Woody: камера включена всегда, если разрешено
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

  // ---------------------------------------------------------------- глаза: камера, жесты, приглашение
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

  function onInvite() {          // 👍 или 👆 перед камерой
    if (!settings.transfer_enabled || Invite.open || Chat.busy) return;
    Face.flash("excited", 1200);
    Voice.speak("Ага, вижу! Сейчас отправлю приглашение.", { rate: 1.05 });
    Invite.start();
  }

  let lastWave = 0;
  function onWave() {            // человек машет рукой — Woody здоровается
    if (Chat.busy || Learn.active || Date.now() - lastWave < 15000) return;
    lastWave = Date.now();
    Face.flash("happy", 1800);
    Voice.speak("Привет! Я тебя вижу.", { rate: 1.05 });
  }

  function stopGesture() { HandTracker.stop(); $("btn-gesture").classList.remove("on"); }

  /** mode: "user" — по просьбе человека (с объяснениями); "auto" — при запуске: спросит разрешение один раз, запрет не обсуждает;
      "silent" — только если уже разрешено. */
  async function startCamera(mode = "user") {
    if (HandTracker.active) return true;
    if (!settings.camera_enabled || (mode !== "user" && !settings.always_on_camera)) return false;
    const say = (t) => { if (mode === "user") Chat.add("woody", t); };
    try {
      const { status } = await api("/permissions/camera/check", { method: "POST" });
      if (status === "denied") { say("Камера запрещена в разрешениях. Я продолжу общаться голосом и в чате."); return false; }
      if (status === "ask") {
        if (mode === "silent") return false;
        const ok = await Permissions.ensure("camera", "Woody хочет использовать фронтальную камеру, чтобы видеть тебя и твои жесты. Видео остаётся на устройстве.", "two");
        if (!ok) { say("Камера запрещена. Я продолжу общаться голосом и в чате."); return false; }
      }
      const on = settings.hand_tracking_enabled;
      await HandTracker.start({ onFist: on ? onFist : null, onInvite: on ? onInvite : null, onWave: on ? onWave : null });
      $("btn-gesture").classList.add("on");
      return true;
    } catch (e) { console.error("[Woody camera]", e); stopGesture(); if (mode !== "silent") Chat.add("woody", e.message || "Не удалось включить камеру.", true); return false; }
  }

  $("btn-gesture").onclick = async () => {
    if (HandTracker.active) return stopGesture();
    if (!settings.camera_enabled) { Chat.add("woody", "Камера выключена в настройках.", true); return; }
    await startCamera("user");
  };
  Device.hooks.camera = async (on) => { if (on) await startCamera("user"); else stopGesture(); };
  Device.hooks.invite = () => Invite.start();
  document.addEventListener("visibilitychange", () => { if (!document.hidden && settings.always_on_camera && !HandTracker.active && Api.hasSession()) startCamera("silent"); });

  // Первое включение: один раз просим всё нужное, чтобы дальше Woody не отвлекал вопросами
  async function allowAll() {
    const perms = await api("/permissions");
    for (const p of perms) if (!p.dangerous && p.decision !== "ALLOW_ALWAYS") await api("/permissions", { method: "POST", body: JSON.stringify({ action: p.action, decision: "ALLOW_ALWAYS" }) });
    await startCamera("silent");
  }

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

      body.append(section("Доступ"));
      const all = h("button", { textContent: "Разрешить Woody всё (кроме удаления файлов)", className: "primary wide" });
      all.onclick = async () => { await allowAll(); renderSettings(); };
      body.append(all);
      body.append(note("Woody делает на устройстве только то, что разрешено ниже. Удаление файлов всегда требует отдельного подтверждения. Браузер не даёт сайтам управлять системой — только камера, микрофон, геолокация, уведомления, буфер обмена и т. п."));

      body.append(section("Camera"));
      body.append(toggleRow("Камера разрешена", "camera_enabled", (on) => { if (!on) stopGesture(); else startCamera("silent"); }));
      body.append(toggleRow("Камера всегда включена (глаза следят)", "always_on_camera", (on) => { if (!on) stopGesture(); else startCamera("user"); }));
      body.append(section("Microphone"));
      body.append(toggleRow("Микрофон разрешён", "microphone_enabled", (on) => { if (!on) Voice.abortListening(); }));
      body.append(section("Voice"));
      body.append(toggleRow("Отвечать голосом", "voice_replies", (on) => Voice.setVoiceReplies(on)));
      const engine = h("select");
      for (const [v, l] of [["natural", "Человеческий голос (сервер)"], ["browser", "Голос браузера"]]) engine.append(h("option", { value: v, textContent: l, selected: settings.voice_engine === v }));
      engine.onchange = async () => { settings = { ...settings, ...(await api("/settings", { method: "PUT", body: JSON.stringify({ voice_engine: engine.value }) })) }; Voice.configure(settings); };
      body.append(h("div", { className: "row" }, h("span", { textContent: "Тип голоса" }), engine));
      const vsel = h("select");
      for (const [v, l] of [["coral", "Коралл (женский, тёплый)"], ["nova", "Нова (женский, живой)"], ["shimmer", "Шиммер (женский, мягкий)"], ["sage", "Сейдж (спокойный)"], ["alloy", "Эллой (нейтральный)"], ["ash", "Эш (мужской)"], ["echo", "Эхо (мужской)"], ["onyx", "Оникс (мужской, низкий)"]]) vsel.append(h("option", { value: v, textContent: l, selected: settings.voice_name === v }));
      vsel.onchange = async () => { settings = { ...settings, ...(await api("/settings", { method: "PUT", body: JSON.stringify({ voice_name: vsel.value }) })) }; Voice.configure(settings); Voice.speak("Привет! Вот так я теперь говорю.", {}); };
      body.append(h("div", { className: "row" }, h("span", { textContent: "Голос" }), vsel));
      const bv = Voice.ruVoices();
      if (bv.length) {
        const bsel = h("select");
        for (const v of bv) bsel.append(h("option", { value: v.voiceURI, textContent: v.name, selected: v.voiceURI === Voice.browserVoiceURI }));
        bsel.onchange = () => { Voice.setBrowserVoice(bsel.value); Voice.configure({ voice_engine: "browser" }); };
        body.append(h("div", { className: "row" }, h("span", { textContent: "Голос браузера" }), bsel));
      }
      body.append(note(`Распознавание речи: ${Voice.supported.stt ? "доступно" : "недоступно в этом браузере"}. Синтез речи: ${Voice.supported.tts ? "доступен" : "недоступен"}.`));

      body.append(section("Gestures"));
      body.append(toggleRow("Hand Tracking", "hand_tracking_enabled", (on) => { if (!on) stopGesture(); }));
      body.append(toggleRow("Передача Woody (Transfer)", "transfer_enabled"));
      body.append(note("Кнопка ✋ включает камеру для жестов. Открытая ладонь → медленно сжать кулак: Woody покажет QR для знакомства с новым человеком. Видео не покидает устройство."));

      body.append(section("Мозг Woody"));
      const br = status.brain || { total: 0, from_ai: 0, self_taught: 0 };
      body.append(note(`Выучено ответов: ${br.total} (из разговоров: ${br.from_ai}, самообучение: ${br.self_taught}). Когда лимит ключа закончится, я отвечаю из этого мозга.`));
      body.append(toggleRow("Учиться на вопросах пользователей", "learn_from_chats"));
      const lb = h("button", { textContent: Learn.active ? "■ Остановить обучение" : "🧠 Обучай самого себя", className: "ghost wide" });
      lb.onclick = async () => { if (Learn.active) { await Learn.stop(); } else { await api("/learn/start", { method: "POST" }); Learn.begin(); drawer.classList.add("hidden"); } };
      body.append(lb);
      const ex = h("button", { textContent: "Сохранить мозг в файл", className: "ghost wide" });
      ex.onclick = async () => {
        const data = await api("/brain/export");
        const url = URL.createObjectURL(new Blob([JSON.stringify(data)], { type: "application/json" }));
        const a = h("a", { href: url, download: "brain_seed.json" }); document.body.append(a); a.click(); a.remove(); URL.revokeObjectURL(url);
      };
      const im = h("button", { textContent: "Загрузить мозг из файла", className: "ghost wide" });
      im.onclick = () => {
        const inp = h("input", { type: "file", accept: "application/json" });
        inp.onchange = async () => { try { const d = JSON.parse(await inp.files[0].text()); const r = await api("/brain/import", { method: "POST", body: JSON.stringify({ items: d.items || [] }) }); Chat.add("woody", `Добавил в мозг ${r.added} новых ответов.`); renderSettings(); } catch (e) { Chat.add("woody", "Не удалось прочитать файл мозга.", true); } };
        inp.click();
      };
      body.append(ex, im);
      body.append(note(status.persistent ? "База данных постоянная: память и мозг переживают перезапуск и деплой." : "⚠ База НЕ постоянная: при деплое всё сотрётся. Подключите Volume или DATABASE_URL (см. README) либо сохраните мозг в файл."));
      if (!status.persistent) body.lastChild.classList.add("warn");

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
