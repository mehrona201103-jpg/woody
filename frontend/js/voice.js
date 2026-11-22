/* Голос Woody.
   Говорит: (1) «человеческим» голосом с сервера (/api/tts, предложение за предложением, с предзагрузкой следующего),
            (2) если сервер недоступен — лучшим русским голосом браузера, с паузами между фразами.
   Слушает: Web Speech API. Микрофон — только с разрешения. */
const Voice = (() => {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  let recognition = null, listening = false, voiceReplies = true;
  let engine = "natural", serverVoice = "coral", serverOk = true, serverFails = 0;
  let playToken = 0, currentAudio = null, audioCtx = null, rafId = 0;
  const supported = { stt: !!SR, tts: "speechSynthesis" in window };
  const micBadge = document.getElementById("mic-badge");
  const VOICE_KEY = "woody_browser_voice";

  function setMic(on) { micBadge.classList.toggle("hidden", !on); }

  // ------------------------------------------------------------ голоса браузера
  function ruVoices() { return supported.tts ? speechSynthesis.getVoices().filter((v) => v.lang.toLowerCase().startsWith("ru")) : []; }
  function pickVoice() {
    const all = ruVoices();
    let saved = null; try { saved = localStorage.getItem(VOICE_KEY); } catch (_) { /* — */ }
    const chosen = saved && all.find((v) => v.voiceURI === saved);
    if (chosen) return chosen;
    // нейронные/«естественные» голоса звучат живее всего
    const score = (v) => (/natural|neural|online|google|milena|yuri|svetlana|dmitry|alena|katya|siri/i.test(v.name) ? 2 : 0) + (v.localService ? 0 : 1);
    return all.sort((a, b) => score(b) - score(a))[0] || null;
  }
  function setBrowserVoice(uri) { try { localStorage.setItem(VOICE_KEY, uri || ""); } catch (_) { /* — */ } }

  function splitSentences(text) {
    const parts = text.replace(/\s+/g, " ").trim().split(/(?<=[.!?…])\s+/);
    const out = [];
    for (const part of parts) {
      if (out.length && (out[out.length - 1].length < 45) && out[out.length - 1].length + part.length < 220) out[out.length - 1] += " " + part;
      else out.push(part);
    }
    return out.filter(Boolean);
  }

  function browserSpeak(text, style, token) {
    return new Promise((resolve) => {
      if (!supported.tts) return resolve();
      const chunks = splitSentences(text);
      let i = 0;
      const next = () => {
        if (token !== playToken || i >= chunks.length) { Face.stopTalking(); return resolve(); }
        const u = new SpeechSynthesisUtterance(chunks[i++]);
        u.lang = "ru-RU"; u.rate = (style.rate || 1) * 1.0; u.pitch = (style.pitch || 1) * 0.98;
        const v = pickVoice(); if (v) u.voice = v;
        u.onstart = () => Face.startTalking();
        u.onboundary = () => Face.wordBoundary();
        u.onend = () => setTimeout(next, 120 + Math.random() * 120);  // живая пауза между фразами
        u.onerror = () => { Face.stopTalking(); resolve(); };
        speechSynthesis.speak(u);
      };
      speechSynthesis.cancel();
      next();
    });
  }

  // ------------------------------------------------------------ человеческий голос с сервера
  function fetchChunk(text, token) {
    return Api.blob("/tts", { text, voice: serverVoice }).then((b) => (token === playToken ? URL.createObjectURL(b) : null));
  }

  function lipSync(audio) {
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      if (audioCtx.state === "suspended") audioCtx.resume();
      const src = audioCtx.createMediaElementSource(audio), an = audioCtx.createAnalyser();
      an.fftSize = 512; src.connect(an); an.connect(audioCtx.destination);
      const buf = new Uint8Array(an.fftSize);
      const loop = () => {
        an.getByteTimeDomainData(buf);
        let sum = 0; for (const b of buf) sum += ((b - 128) / 128) ** 2;
        Face.setMouthLevel(Math.min(1, Math.sqrt(sum / buf.length) * 5));
        rafId = requestAnimationFrame(loop);
      };
      loop();
    } catch (_) { Face.setMouthLevel(null); }
  }

  function playUrl(url, token) {
    return new Promise((resolve) => {
      const audio = new Audio(url); currentAudio = audio;
      audio.onplay = () => { Face.startTalking(); lipSync(audio); };
      const done = () => { cancelAnimationFrame(rafId); Face.setMouthLevel(null); URL.revokeObjectURL(url); if (currentAudio === audio) currentAudio = null; resolve(); };
      audio.onended = done; audio.onerror = done;
      if (token !== playToken) return done();
      audio.play().catch(done);
    });
  }

  async function naturalSpeak(text, token) {
    const chunks = splitSentences(text);
    let pending = fetchChunk(chunks[0], token);
    for (let i = 0; i < chunks.length; i++) {
      let url;
      try { url = await pending; } catch (e) { return { ok: false, rest: chunks.slice(i).join(" ") }; }
      if (token !== playToken) return { ok: true };
      pending = i + 1 < chunks.length ? fetchChunk(chunks[i + 1], token) : null;
      if (pending) pending.catch(() => {});
      if (url) await playUrl(url, token);
    }
    return { ok: true };
  }

  async function speak(text, style = {}) {
    if (!voiceReplies || !text) return;
    const token = ++playToken;
    stopAudioOnly();
    if (engine === "natural" && serverOk) {
      const res = await naturalSpeak(text, token);
      if (res.ok) { serverFails = 0; Face.stopTalking(); return; }
      if (++serverFails >= 2) serverOk = false; // сервер без голоса — дальше сразу браузерный
      if (token !== playToken) return;
      return browserSpeak(res.rest || text, style, token);
    }
    return browserSpeak(text, style, token);
  }

  function stopAudioOnly() {
    if (currentAudio) { try { currentAudio.pause(); } catch (_) { /* — */ } currentAudio = null; }
    cancelAnimationFrame(rafId); Face.setMouthLevel(null);
    if (supported.tts) speechSynthesis.cancel();
  }

  /** Немедленно прерывает речь Woody. */
  function stopSpeaking() { playToken++; stopAudioOnly(); Face.stopTalking(); }

  function configure(s) {
    if (s.voice_engine) engine = s.voice_engine;
    if (s.voice_name) serverVoice = s.voice_name;
    if (s.voice_engine || s.voice_name) { serverOk = true; serverFails = 0; }
  }

  // ------------------------------------------------------------ слушание
  async function listen() {
    if (!supported.stt) throw new Error("Этот браузер не поддерживает распознавание речи. Напиши мне в чат или открой Woody в Chrome/Edge/Safari.");
    const ok = await Permissions.ensure("microphone", "Woody хочет использовать микрофон.");
    if (!ok) throw new Error("Микрофон запрещён. Ты можешь писать мне в чат.");
    return new Promise((resolve, reject) => {
      recognition = new SR();
      recognition.lang = "ru-RU"; recognition.interimResults = false; recognition.maxAlternatives = 1;
      let got = "";
      recognition.onresult = (e) => { got = e.results[0][0].transcript; };
      recognition.onerror = (e) => {
        listening = false; setMic(false);
        console.error("[Woody STT]", e.error);
        const map = {
          "not-allowed": "Браузер не дал доступ к микрофону. Разреши его в настройках сайта или напиши мне в чат.",
          "service-not-allowed": "Распознавание речи заблокировано браузером. Напиши мне в чат.",
          "no-speech": "Я ничего не услышал. Попробуй ещё раз.",
          "audio-capture": "Микрофон не найден. Напиши мне в чат.",
          "network": "Нет сети для распознавания речи. Напиши мне в чат.",
          "aborted": "",
        };
        reject(new Error(e.error in map ? map[e.error] : "Ошибка микрофона. Напиши мне в чат."));
      };
      recognition.onend = () => { listening = false; setMic(false); if (got) resolve(got); else reject(new Error("")); };
      try { listening = true; setMic(true); recognition.start(); }
      catch (err) { listening = false; setMic(false); console.error("[Woody STT]", err); reject(new Error("Не удалось запустить микрофон. Напиши мне в чат.")); }
    });
  }

  function abortListening() {
    if (recognition && listening) { try { recognition.abort(); } catch (_) { /* уже остановлен */ } }
    listening = false; setMic(false);
  }

  if (supported.tts) speechSynthesis.onvoiceschanged = () => {};
  return { supported, speak, stopSpeaking, listen, abortListening, configure, ruVoices, setBrowserVoice,
           get browserVoiceURI() { const v = pickVoice(); return v ? v.voiceURI : ""; },
           setVoiceReplies: (v) => { voiceReplies = v; if (!v) stopSpeaking(); }, get voiceReplies() { return voiceReplies; } };
})();
