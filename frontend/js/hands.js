/* Глаза Woody: фронтальная камера работает всё время (с разрешения), всё локально в браузере.
   • MediaPipe Hands — жесты (ладонь→кулак, 👍/👆 приглашение, махание) и положение руки;
   • детектор движения по кадрам — Woody замечает любое движение в кадре и смотрит туда.
   Видео на сервер не отправляется (кадр уходит только по прямой просьбе «что ты видишь»). */
const HandTracker = (() => {
  const MP = "https://cdn.jsdelivr.net/npm/@mediapipe/hands@0.4.1675469240/";
  const video = document.getElementById("cam-video");
  const wrap = document.getElementById("cam-wrap");
  const badge = document.getElementById("cam-badge");
  const label = document.getElementById("cam-gesture");
  let stream = null, hands = null, running = false, busy = false, lastSend = 0, cbs = {};
  let handsOk = false, handSeenAt = 0, prevFrame = null, lastMotionAt = 0, lastSurprise = 0, wakeLock = null;
  const transferDetector = new Gestures.TransferDetector();
  const inviteDetector = new Gestures.InviteDetector();
  const waveDetector = new Gestures.WaveDetector();
  const motionCanvas = document.createElement("canvas"); motionCanvas.width = 40; motionCanvas.height = 30;
  const mctx = motionCanvas.getContext("2d", { willReadFrequently: true });

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

  async function keepAwake() {
    try { if ("wakeLock" in navigator) wakeLock = await navigator.wakeLock.request("screen"); } catch (_) { /* не критично */ }
  }

  /** callbacks: { onFist, onInvite, onWave } */
  async function start(callbacks = {}) {
    if (running) return;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) throw new Error("Камера недоступна в этом браузере (нужен HTTPS).");
    cbs = callbacks;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } }, audio: false });
    } catch (e) { console.error("[Woody camera]", e); throw cameraError(e); }
    video.srcObject = stream;
    stream.getVideoTracks().forEach((t) => { t.onended = () => { if (running) stop(); }; });
    wrap.classList.remove("hidden"); badge.classList.remove("hidden");
    try { await video.play(); } catch (_) { /* muted playsinline обычно достаточно */ }
    keepAwake();
    running = true; prevFrame = null;
    transferDetector.reset(); inviteDetector.reset();
    // Жесты рук грузятся отдельно: если CDN недоступен, глаза всё равно следят за движением.
    handsOk = false;
    try {
      if (!window.Hands) await loadScript(MP + "hands.js");
      hands = new window.Hands({ locateFile: (f) => MP + f });
      hands.setOptions({ maxNumHands: 1, modelComplexity: 0, minDetectionConfidence: 0.6, minTrackingConfidence: 0.5 });
      hands.onResults(onResults);
      handsOk = true;
    } catch (e) { console.error("[Woody hands]", e); hands = null; }
    requestAnimationFrame(loop);
  }

  function lookAtFrame(nx, ny, ttl) { // nx,ny — 0..1 в кадре; превью зеркальное: правая рука пользователя справа на экране
    Face.lookAt(1 - 2 * nx, 2 * ny - 1, ttl);
  }

  function motionStep(now) {
    if (video.readyState < 2) return;
    mctx.drawImage(video, 0, 0, 40, 30);
    const data = mctx.getImageData(0, 0, 40, 30).data;
    const gray = new Uint8Array(1200);
    for (let i = 0; i < 1200; i++) gray[i] = (data[i * 4] * 0.3 + data[i * 4 + 1] * 0.59 + data[i * 4 + 2] * 0.11) | 0;
    if (prevFrame) {
      let n = 0, sx = 0, sy = 0;
      for (let i = 0; i < 1200; i++) if (Math.abs(gray[i] - prevFrame[i]) > 28) { n++; sx += i % 40; sy += (i / 40) | 0; }
      const ratio = n / 1200;
      if (ratio > 0.012 && ratio < 0.7) {
        lastMotionAt = now;
        if (now - handSeenAt > 400) lookAtFrame(sx / n / 40, sy / n / 30, 1300); // рука в кадре важнее общего движения
        if (ratio > 0.3 && now - lastSurprise > 5000) { lastSurprise = now; Face.flash("surprised", 700); }
      }
    }
    prevFrame = gray;
  }

  async function loop(now) {
    if (!running) return;
    if (now - lastSend > 66 && video.readyState >= 2) { // ~15 кадров/с
      lastSend = now;
      if (!busy) {
        busy = true;
        try {
          if (handsOk) await hands.send({ image: video });
        } catch (e) { console.error("[Woody hands] send", e); }
        busy = false;
      }
      if (Math.round(now / 66) % 2 === 0) { try { motionStep(now); } catch (e) { console.error("[Woody motion]", e); } }
      if (!handsOk || now - handSeenAt > 700) label.textContent = now - lastMotionAt < 800 ? "вижу движение" : "смотрю";
    }
    requestAnimationFrame(loop);
  }

  function onResults(res) {
    const lm = res.multiHandLandmarks && res.multiHandLandmarks[0];
    const now = performance.now();
    const gesture = lm ? Gestures.classify(lm) : null;
    if (lm) {
      handSeenAt = now;
      lookAtFrame(lm[9].x, lm[9].y, 1100);   // глаза следят за рукой
      label.textContent = gesture ? gesture.replace("_", " ") : "рука";
    }
    if (transferDetector.update(gesture, now) && cbs.onFist) cbs.onFist();
    else if (inviteDetector.update(gesture, now) && cbs.onInvite) cbs.onInvite();
    else if (lm && waveDetector.update(gesture, lm[0].x, now) && cbs.onWave) cbs.onWave();
  }

  /** Снимок текущего кадра (JPEG data URL, ≤ 640 px) — только по прямой просьбе пользователя. */
  function snapshot(maxW = 512) {
    if (!running || video.readyState < 2) return null;
    const w = Math.min(maxW, video.videoWidth || maxW), h = Math.round(w * (video.videoHeight || 3) / (video.videoWidth || 4));
    const c = document.createElement("canvas"); c.width = w; c.height = h;
    c.getContext("2d").drawImage(video, 0, 0, w, h);
    return c.toDataURL("image/jpeg", 0.8);
  }

  function stop() {
    running = false;
    if (stream) stream.getTracks().forEach((t) => t.stop());   // камера реально выключается
    stream = null; video.srcObject = null;
    if (hands && hands.close) { try { hands.close(); } catch (_) { /* уже закрыт */ } }
    hands = null; handsOk = false;
    if (wakeLock) { try { wakeLock.release(); } catch (_) { /* — */ } wakeLock = null; }
    wrap.classList.add("hidden"); badge.classList.add("hidden"); label.textContent = "";
  }

  return { start, stop, snapshot, get active() { return running; } };
})();
