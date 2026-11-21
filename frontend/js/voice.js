/* Голос: STT и TTS работают в браузере (Web Speech API). Микрофон — только с разрешения. */
const Voice = (() => {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  let recognition = null, listening = false, voiceReplies = true;
  const supported = { stt: !!SR, tts: "speechSynthesis" in window };
  const micBadge = document.getElementById("mic-badge");

  function setMic(on) { micBadge.classList.toggle("hidden", !on); }
  function pickVoice() { return speechSynthesis.getVoices().find((v) => v.lang.toLowerCase().startsWith("ru")) || null; }

  function speak(text, style = {}) {
    return new Promise((resolve) => {
      if (!voiceReplies || !supported.tts) return resolve();
      speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.lang = "ru-RU"; u.rate = style.rate || 1; u.pitch = style.pitch || 1;
      const v = pickVoice(); if (v) u.voice = v;
      u.onstart = () => Face.startTalking();
      u.onboundary = () => Face.wordBoundary();
      u.onend = u.onerror = () => { Face.stopTalking(); resolve(); };
      speechSynthesis.speak(u);
    });
  }

  /** Немедленно прерывает речь Woody. */
  function stopSpeaking() {
    if (supported.tts) speechSynthesis.cancel();
    Face.stopTalking();
  }

  /** Слушает одну фразу. Возвращает текст или бросает Error с понятным сообщением. */
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

  return { supported, speak, stopSpeaking, listen, abortListening,
           setVoiceReplies: (v) => { voiceReplies = v; if (!v) stopSpeaking(); }, get voiceReplies() { return voiceReplies; } };
})();
