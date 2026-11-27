/* Экран входа и регистрации: почта + пароль + имя, вход через Google, голосовое приветствие Woody. */
const Auth = (() => {
  const $ = (id) => document.getElementById(id);
  const view = $("auth"), tabs = document.querySelector(".tabs"), main = $("auth-main");
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  let mode = "register", cfg = { google_client_id: null }, busy = false, onDone = null, greeted = false, typeTimer = 0, gsiReady = null;
  const WELCOME = "Привет! Я Woody. Чтобы начать общаться со мной, зарегистрируйся. А если мы уже знакомы, просто войди.";

  // ---------- реплика Woody с печатью
  function bubble(text) {
    clearInterval(typeTimer);
    const el = $("auth-bubble"); el.textContent = "";
    let i = 0; typeTimer = setInterval(() => { el.textContent = text.slice(0, ++i); if (i >= text.length) clearInterval(typeTimer); }, 22);
  }

  // ---------- голосовое приветствие (публичная фиксированная фраза)
  async function speakGreeting(kind) {
    try {
      const res = await fetch((window.WOODY_API_BASE || "") + "/api/tts/greeting?kind=" + kind);
      if (!res.ok) return;
      const url = URL.createObjectURL(await res.blob()), a = new Audio(url);
      a.onplaying = () => Face.startTalking(); a.onended = a.onerror = () => { Face.stopTalking(); URL.revokeObjectURL(url); };
      try { await a.play(); }
      catch (_) {   // автозвук заблокирован — играем при первом касании
        const go = () => { a.play().catch(() => {}); removeEventListener("pointerdown", go); };
        addEventListener("pointerdown", go, { once: true });
      }
    } catch (e) { console.warn("[Woody greeting]", e); }
  }

  function setError(msg) {
    const el = $("auth-error"); el.textContent = msg || "";
    if (msg) { el.classList.remove("shake"); void el.offsetWidth; el.classList.add("shake"); }
  }
  function setBusy(btn, on) { busy = on; btn.classList.toggle("loading", on); btn.disabled = on; }

  function setMode(m, speak = true) {
    mode = m;
    const login = m === "login";
    tabs.classList.toggle("login", login); main.classList.toggle("login", login);
    $("tab-register").classList.toggle("active", !login); $("tab-login").classList.toggle("active", login);
    $("auth-submit").querySelector(".lbl").textContent = login ? "Войти" : "Создать аккаунт";
    $("auth-pass").autocomplete = login ? "current-password" : "new-password";
    $("auth-pass").placeholder = login ? "Твой пароль" : "Минимум 6 символов";
    const referralField = document.querySelector(".referral-field"); if (referralField) referralField.classList.toggle("hidden", login);
    setError("");
    if (speak) bubble(login ? "С возвращением! Введи почту и пароль." : WELCOME);
  }

  function finish(res) {
    Api.setSession(res.session);
    if (onDone) onDone({ name: res.name, isNew: !!res.new });
  }

  async function submit(e) {
    e.preventDefault(); if (busy) return;
    const email = $("auth-email").value.trim(), pass = $("auth-pass").value, name = $("auth-name").value.trim();
    if (mode === "register" && !name) return setError("Напиши, как мне тебя называть.");
    if (!email) return setError("Введи почту.");
    if (!pass) return setError("Введи пароль.");
    setError(""); Voice.unlock();
    const btn = $("auth-submit"); setBusy(btn, true);
    try {
      const ref = (new URLSearchParams(location.search).get("ref") || $("auth-referral").value || "").trim();
      const payload = { email, password: pass, name };
      if (mode === "register" && ref) payload.referral_code = ref;
      const res = await Api.request(mode === "login" ? "/auth/login" : "/auth/register", { method: "POST", body: JSON.stringify(payload) });
      finish(res);
    } catch (err) { Face.flash("sad", 1200); setError(err.userMessage || "Не получилось. Попробуй ещё раз."); }
    finally { setBusy(btn, false); }
  }

  // ---------- Google
  function loadGsi() {
    if (gsiReady) return gsiReady;
    gsiReady = new Promise((resolve, reject) => {
      if (window.google && google.accounts && google.accounts.oauth2) return resolve();
      const s = document.createElement("script"); s.src = "https://accounts.google.com/gsi/client"; s.async = true;
      s.onload = resolve; s.onerror = () => { gsiReady = null; reject(new Error("gsi")); };
      document.head.appendChild(s);
    });
    return gsiReady;
  }

  async function google_() {
    if (busy) return;
    if (!cfg.google_client_id) return setError("Вход через Google ещё не включён: владельцу нужно добавить GOOGLE_CLIENT_ID в Variables.");
    setError(""); Voice.unlock();
    const btn = $("google-btn"); setBusy(btn, true);
    try {
      await loadGsi();
      const token = await new Promise((resolve, reject) => {
        const client = google.accounts.oauth2.initTokenClient({
          client_id: cfg.google_client_id, scope: "openid email profile",
          callback: (r) => (r.access_token ? resolve(r.access_token) : reject(new Error(r.error || "google"))),
          error_callback: (er) => reject(new Error(er && er.type ? er.type : "google")),
        });
        client.requestAccessToken({ prompt: "select_account" });
      });
      const ref = (new URLSearchParams(location.search).get("ref") || $("auth-referral").value || "").trim();
      finish(await Api.request("/auth/google", { method: "POST", body: JSON.stringify({ access_token: token, referral_code: ref || null }) }));
    } catch (err) {
      if (!/popup_closed|access_denied/.test(err.message || "")) setError(err.userMessage || "Не удалось войти через Google. Попробуй ещё раз.");
    } finally { setBusy(btn, false); }
  }

  // ---------- режим присоединения по QR (/join/…): тот же экран, только имя
  const intro = {
    show() { view.classList.add("active"); $("join-box").classList.remove("hidden"); main.classList.add("hidden"); $("auth-bubble").classList.add("hidden"); Face.mount($("auth-face")); },
    hide() { $("join-box").classList.add("hidden"); $("name-form").classList.add("hidden"); },
    async say(line, emotion) { if (emotion) Face.setEmotion(emotion); $("intro-text").textContent = line; Face.setActivity("speaking"); await sleep(Math.min(2200, 500 + line.length * 45)); Face.setActivity("idle"); },
    async greet(lines) { this.show(); for (const l of lines) { await this.say(l, "happy"); await sleep(200); } },
    askName() {
      return new Promise((resolve) => {
        const form = $("name-form"); form.classList.remove("hidden"); $("name-input").focus();
        form.onsubmit = (e) => { e.preventDefault(); const n = $("name-input").value.trim(); if (n) { form.classList.add("hidden"); resolve(n); } };
      });
    },
    error(msg) { this.show(); $("name-form").classList.add("hidden"); $("intro-text").textContent = msg; Face.setEmotion("sad"); },
  };

  function init() {
    $("tab-register").onclick = () => setMode("register");
    $("tab-login").onclick = () => setMode("login");
    $("auth-form").onsubmit = submit;
    const ref = new URLSearchParams(location.search).get("ref"); if (ref && $("auth-referral")) $("auth-referral").value = ref;
    $("google-btn").onclick = google_;
    $("pw-eye").onclick = () => { const p = $("auth-pass"); p.type = p.type === "password" ? "text" : "password"; };
    Api.request("/auth/config").then((c) => { cfg = c; if (c.google_client_id) loadGsi().catch(() => {}); }).catch(() => {});
  }

  function show() {
    main.classList.remove("hidden"); $("auth-bubble").classList.remove("hidden"); $("join-box").classList.add("hidden");
    view.classList.add("active"); Face.mount($("auth-face")); Face.setEmotion("happy"); Face.setActivity("idle");
    setMode(mode, false);
    if (!greeted) { greeted = true; bubble(WELCOME); speakGreeting("welcome"); } else bubble(mode === "login" ? "С возвращением! Введи почту и пароль." : WELCOME);
  }
  function hide() { view.classList.remove("active"); clearInterval(typeTimer); }

  return { init, show, hide, intro, error: setError, set onDone(fn) { onDone = fn; } };
})();
