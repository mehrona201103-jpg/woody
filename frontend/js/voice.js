/* Голос Woody — только живой голос с сервера (/api/tts), голос браузера НЕ используется.
   Быстро: фразы синтезируются параллельно и играют по очереди (первая — короткая).
   Эмоции: тон ответа (смех, грусть, слёзы…) передаётся в синтез.
   Слушает: Web Speech API, непрерывно, с автоперезапуском; на время ответа Woody слух ставится на паузу. */
const Voice = (() => {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const supported = { stt: !!SR };
  const micBadge = document.getElementById("mic-badge");
  let voiceName = "coral", replies = true;
  let token = 0, audio = null, actx = null, analyser = null, buf = null, raf = 0, wired = false, warned = false;

  // ------------------------------------------------------------ воспроизведение
  function ensureAudio() {
    if (audio) return audio;
    audio = new Audio(); audio.playsInline = true; audio.preload = "auto";
    return audio;
  }
  /** Вызывать из обработчика нажатия: снимает блокировку автозвука на телефонах. */
  function unlock() {
    try {
      const a = ensureAudio();
      actx = actx || new (window.AudioContext || window.webkitAudioContext)();
      if (actx.state === "suspended") actx.resume();
      if (!a.src) { a.src = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAESsAACJWAAACABAAZGF0YQAAAAA="; a.play().catch(() => {}); }
    } catch (_) { /* не критично */ }
  }
  function wire() {
    if (wired) return; wired = true;
    try {
      actx = actx || new (window.AudioContext || window.webkitAudioContext)();
      const src = actx.createMediaElementSource(ensureAudio());
      analyser = actx.createAnalyser(); analyser.fftSize = 256; buf = new Uint8Array(analyser.fftSize);
      src.connect(analyser); analyser.connect(actx.destination);
    } catch (_) { analyser = null; }
  }
  function lipLoop() {
    cancelAnimationFrame(raf);
    const step = () => {
      if (analyser) {
        analyser.getByteTimeDomainData(buf);
        let sum = 0; for (let i = 0; i < buf.length; i++) { const v = (buf[i] - 128) / 128; sum += v * v; }
        Face.setMouthLevel(Math.min(1, Math.sqrt(sum / buf.length) * 5.5));
      }
      raf = requestAnimationFrame(step);
    };
    step();
  }

  function split(text) {
    const parts = text.replace(/\s+/g, " ").trim().split(/(?<=[.!?…])\s+/).filter(Boolean);
    const out = [];
    for (const p of parts) {
      if (out.length > 0 && out[out.length - 1].length < 40 && out[out.length - 1].length + p.length < 200) out[out.length - 1] += " " + p;
      else out.push(p);
    }
    // первая фраза короткая — звук стартует быстрее
    if (out[0] && out[0].length > 90) {
      const cut = Math.max(out[0].lastIndexOf(", ", 80), out[0].lastIndexOf(" ", 80));
      if (cut > 25) { const rest = out[0].slice(cut + 1).trim(); out[0] = out[0].slice(0, cut + 1).trim(); out.splice(1, 0, rest); }
    }
    while (out.length > 6) { const last = out.pop(); out[out.length - 1] += " " + last; }
    return out;
  }

  function fetchChunk(text, tone, my) {
    return Api.blob("/tts", { text: text.slice(0, 690), voice: voiceName, tone }).then(
      (b) => (my === token ? URL.createObjectURL(b) : null),
      (e) => { console.error("[Woody TTS]", e); return null; });
  }

  function playUrl(url, my) {
    return new Promise((resolve) => {
      const a = ensureAudio(); wire();
      const done = () => { a.onended = a.onerror = a.onplaying = null; URL.revokeObjectURL(url); resolve(); };
      a.onplaying = () => { Face.startTalking(); lipLoop(); };
      a.onended = done; a.onerror = done;
      if (my !== token) return done();
      a.src = url;
      if (actx && actx.state === "suspended") actx.resume();
      a.play().catch((e) => { console.error("[Woody audio]", e); done(); });
    });
  }

  /** Говорит текст живым голосом. Возвращает промис, который завершается, когда Woody договорил. */
  async function speak(text, opts = {}) {
    if (!replies || !text) return;
    const my = ++token; stopAudioOnly();
    const tone = opts.tone || "calm", chunks = split(text);
    const jobs = chunks.map((c) => fetchChunk(c, tone, my));
    let played = false;
    for (let i = 0; i < jobs.length; i++) {
      const url = await jobs[i];
      if (my !== token) return;
      if (url) { played = true; await playUrl(url, my); }
    }
    if (my !== token) return;
    cancelAnimationFrame(raf); Face.setMouthLevel(null); Face.stopTalking();
    if (!played && !warned) { warned = true; window.dispatchEvent(new CustomEvent("woody:novoice")); }
  }

  function stopAudioOnly() {
    if (audio) { try { audio.pause(); } catch (_) { /* — */ } }
    cancelAnimationFrame(raf); Face.setMouthLevel(null);
  }
  /** Немедленно прерывает речь Woody. */
  function stopSpeaking() { token++; stopAudioOnly(); Face.stopTalking(); }

  function configure(s) { if (s && s.voice_name) voiceName = s.voice_name; }

  // ------------------------------------------------------------ слушание (непрерывно)
  let rec = null, want = false, paused = false, listening = false, h = {};
  function setMic(on) { micBadge.classList.toggle("hidden", !on); }

  function build() {
    rec = new SR(); rec.lang = "ru-RU"; rec.continuous = false; rec.interimResults = true; rec.maxAlternatives = 1;
    rec.onresult = (e) => {
      let interim = "", fin = "";
      for (let i = e.resultIndex; i < e.results.length; i++) { const r = e.results[i]; (r.isFinal ? (fin += r[0].transcript) : (interim += r[0].transcript)); }
      if (interim && h.onInterim) h.onInterim(interim);
      if (fin.trim() && h.onFinal) h.onFinal(fin.trim());
    };
    rec.onerror = (e) => {
      console.warn("[Woody STT]", e.error);
      if (e.error === "not-allowed" || e.error === "service-not-allowed") { want = false; setMic(false); if (h.onError) h.onError("denied"); }
      else if (e.error === "audio-capture") { want = false; setMic(false); if (h.onError) h.onError("nomic"); }
    };
    rec.onend = () => { listening = false; if (want && !paused) setTimeout(begin, 120); else setMic(false); };
  }
  function begin() {
    if (!want || paused || listening) return;
    if (!rec) build();
    try { rec.start(); listening = true; setMic(true); if (h.onListening) h.onListening(true); }
    catch (_) { setTimeout(begin, 350); }
  }

  /** Включает разговорный режим: микрофон слушает постоянно. handlers: onInterim, onFinal, onListening, onError. */
  async function startConversation(handlers) {
    if (!SR) throw new Error("Этот браузер не распознаёт речь. Открой Woody в Chrome, Edge или Safari — или пиши в чат.");
    const ok = await Permissions.ensure("microphone", "Woody хочет слышать тебя через микрофон.", "two");
    if (!ok) throw new Error("Микрофон запрещён. Ты можешь писать мне в чат.");
    h = handlers || {}; want = true; paused = false; unlock(); begin();
  }
  function stopConversation() { want = false; paused = false; if (rec && listening) { try { rec.abort(); } catch (_) { /* — */ } } listening = false; setMic(false); }
  /** Пауза слуха (пока Woody думает и говорит — чтобы он не слышал сам себя). */
  function pause() { paused = true; if (rec && listening) { try { rec.abort(); } catch (_) { /* — */ } } listening = false; if (h.onListening) h.onListening(false); }
  function resume() { if (!want) return; paused = false; setTimeout(begin, 120); }
  function mute(m) { if (m) { pause(); setMic(false); } else resume(); }

  return { supported, speak, stopSpeaking, unlock, configure, startConversation, stopConversation, pause, resume, mute,
           setVoiceReplies: (v) => { replies = v; if (!v) stopSpeaking(); }, get voiceReplies() { return replies; },
           get active() { return want; } };
})();
