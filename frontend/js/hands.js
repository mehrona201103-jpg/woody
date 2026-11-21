/* Hand tracking: фронтальная камера + MediaPipe Hands, всё локально в браузере. Видео на сервер не отправляется. */
const HandTracker = (() => {
  const MP = "https://cdn.jsdelivr.net/npm/@mediapipe/hands@0.4.1675469240/";
  const video = document.getElementById("cam-video");
  const wrap = document.getElementById("cam-wrap");
  const badge = document.getElementById("cam-badge");
  const label = document.getElementById("cam-gesture");
  let stream = null, hands = null, running = false, busy = false, lastSend = 0, onTransfer = null;
  const detector = new Gestures.TransferDetector();

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src; s.crossOrigin = "anonymous"; s.onload = resolve;
      s.onerror = () => reject(new Error("Не удалось загрузить " + src));
      document.head.appendChild(s);
    });
  }

  function cameraError(e) {
    const map = {
      NotAllowedError: "Браузер не дал доступ к камере. Разрешите её в настройках сайта.",
      NotFoundError: "Камера не найдена.",
      NotReadableError: "Камера занята другим приложением.",
      OverconstrainedError: "Фронтальная камера недоступна.",
      SecurityError: "Камера доступна только по HTTPS.",
    };
    return new Error(map[e.name] || "Ошибка камеры: " + (e.name || e.message));
  }

  async function start(transferCb) {
    if (running) return;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) throw new Error("Камера недоступна в этом браузере (нужен HTTPS).");
    onTransfer = transferCb;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } }, audio: false });
    } catch (e) { console.error("[Woody camera]", e); throw cameraError(e); }
    video.srcObject = stream;
    wrap.classList.remove("hidden"); badge.classList.remove("hidden");
    try { await video.play(); } catch (_) { /* autoplay политики — muted playsinline обычно достаточно */ }
    try {
      if (!window.Hands) await loadScript(MP + "hands.js");
      hands = new window.Hands({ locateFile: (f) => MP + f });
      hands.setOptions({ maxNumHands: 1, modelComplexity: 0, minDetectionConfidence: 0.6, minTrackingConfidence: 0.5 });
      hands.onResults(onResults);
    } catch (e) {
      console.error("[Woody hands]", e);
      stop();
      throw new Error("Не удалось загрузить распознавание жестов. Проверьте интернет.");
    }
    running = true; detector.reset();
    requestAnimationFrame(loop);
  }

  async function loop(now) {
    if (!running) return;
    if (!busy && now - lastSend > 66 && video.readyState >= 2) { // ~15 кадров/с
      busy = true; lastSend = now;
      try { await hands.send({ image: video }); }
      catch (e) { console.error("[Woody hands] send", e); }
      busy = false;
    }
    requestAnimationFrame(loop);
  }

  function onResults(res) {
    const lm = res.multiHandLandmarks && res.multiHandLandmarks[0];
    const gesture = lm ? Gestures.classify(lm) : null;
    label.textContent = gesture ? gesture.replace("_", " ") : (lm ? "…" : "рука не видна");
    if (detector.update(gesture, performance.now()) && onTransfer) onTransfer();
  }

  function stop() {
    running = false;
    if (stream) stream.getTracks().forEach((t) => t.stop());   // камера реально выключается
    stream = null; video.srcObject = null;
    if (hands && hands.close) { try { hands.close(); } catch (_) { /* уже закрыт */ } }
    hands = null;
    wrap.classList.add("hidden"); badge.classList.add("hidden"); label.textContent = "";
  }

  return { start, stop, get active() { return running; } };
})();
