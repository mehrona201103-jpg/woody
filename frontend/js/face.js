/* Face Engine. Каждый кадр плавно приближает параметры лица к целевым (lerp).
   Состояния: neutral listening thinking speaking happy sad angry surprised confused sleepy excited.
   Вход от сервера: Face.applyState({emotion, activity, energy}). */
const Face = (() => {
  const $ = (id) => document.getElementById(id);
  const NS = "http://www.w3.org/2000/svg";
  const eyeL = $("eye-l"), eyeR = $("eye-r"), mouthEl = $("mouth");
  const browL = $("brow-l"), browR = $("brow-r");
  const lidL = eyeL.querySelector(".lid"), lidR = eyeR.querySelector(".lid");
  const pupilL = eyeL.querySelector(".pupil-g"), pupilR = eyeR.querySelector(".pupil-g");
  const svg = $("face");

  // lidIn/lidOut — насколько прикрыто веко у внутреннего/внешнего угла глаза (0..1)
  // smile: -1 грусть … 1 улыбка; open: 0..1 раскрытие рта; width: ширина рта; brow: [высота, наклон]
  const T = {
    neutral:   { lidIn: .00, lidOut: .00, scale: 1.00, smile: .10,  open: 0,   width: 50, browY: 0,   browTilt: 0,   look: null },
    listening: { lidIn: .00, lidOut: .00, scale: 1.08, smile: .05,  open: 0,   width: 46, browY: -8,  browTilt: -2,  look: null },
    thinking:  { lidIn: .12, lidOut: .22, scale: 1.00, smile: -.05, open: 0,   width: 36, browY: -4,  browTilt: 6,   look: [-14, -12] },
    speaking:  { lidIn: .00, lidOut: .00, scale: 1.00, smile: .20,  open: 0,   width: 50, browY: -3,  browTilt: 0,   look: null },
    happy:     { lidIn: .00, lidOut: .22, scale: 1.00, smile: 1.00, open: 0,   width: 58, browY: -5,  browTilt: -3,  look: null },
    excited:   { lidIn: .00, lidOut: .00, scale: 1.12, smile: 1.00, open: .45, width: 60, browY: -12, browTilt: -4,  look: null },
    sad:       { lidIn: .00, lidOut: .35, scale: 1.00, smile: -.90, open: 0,   width: 44, browY: 2,   browTilt: 12,  look: [0, 8] },
    angry:     { lidIn: .38, lidOut: .00, scale: 1.00, smile: -.40, open: 0,   width: 48, browY: 4,   browTilt: -14, look: null },
    surprised: { lidIn: .00, lidOut: .00, scale: 1.18, smile: 0,    open: .85, width: 26, browY: -14, browTilt: 0,   look: null },
    confused:  { lidIn: .06, lidOut: .10, scale: 1.00, smile: -.10, open: 0,   width: 44, browY: -6,  browTilt: 10,  look: [10, -4] },
    learning:  { lidIn: 1.00, lidOut: 1.00, scale: 1.00, smile: .35,  open: 0,   width: 42, browY: -6,  browTilt: -3,  look: [0, 0] },
    sleepy:    { lidIn: .60, lidOut: .60, scale: 1.00, smile: .00,  open: 0,   width: 40, browY: 4,   browTilt: 0,   look: [0, 6] },
  };
  const KEYS = ["lidIn", "lidOut", "scale", "smile", "open", "width", "browY", "browTilt"];

  const cur = { ...T.neutral, look: undefined };
  const look = { x: 0, y: 0, tx: 0, ty: 0 };
  let emotion = "neutral", activity = "idle", energy = 60;
  let blink = 0, blinkPhase = 0, talking = false, talkLevel = 0, talkTarget = 0, pulseUntil = 0, mouthOverride = null;
  let lastT = performance.now();
  let gaze = null, pupilScale = 1, flashUntil = 0, flashEmotion = null;

  function target() {
    // activity имеет приоритет над эмоцией, но речь сохраняет улыбку/грусть эмоции
    if (activity === "learning") return { ...T.learning, name: "learning" };
    if (flashEmotion && performance.now() < flashUntil && activity === "idle") return { ...T[flashEmotion], name: flashEmotion };
    if (activity === "listening") return { ...T.listening, name: "listening" };
    if (activity === "thinking") return { ...T.thinking, name: "thinking" };
    const t = { ...(T[emotion] || T.neutral), name: emotion };
    if (activity === "speaking") { t.browY = Math.min(t.browY, -3); }
    return t;
  }

  function path(m) {
    const cx = 200, cy = 224, w = m.width;
    const cornerY = cy - m.smile * 10;
    const curve = cy + m.smile * 30;
    const open = Math.max(0, m.open);
    if (open < 0.03) return { d: `M${cx - w} ${cornerY} Q${cx} ${curve} ${cx + w} ${cornerY}`, fill: 0 };
    const up = curve - 14 + open * 4, low = curve + 14 + open * 68;
    return { d: `M${cx - w} ${cornerY} Q${cx} ${up} ${cx + w} ${cornerY} Q${cx} ${low} ${cx - w} ${cornerY} Z`, fill: Math.min(1, open * 3) };
  }

  function lidPath(inner, outer, blinkAmt, side) {
    // side: -1 левый глаз (внутренний угол справа), +1 правый (внутренний слева)
    const rin = Math.min(1, inner + blinkAmt), rout = Math.min(1, outer + blinkAmt);
    const yIn = -50 + rin * 100, yOut = -50 + rout * 100;
    const xl = side < 0 ? yOut : yIn, xr = side < 0 ? yIn : yOut;
    return `M-48 -52 L48 -52 L48 ${xr} L-48 ${xl} Z`;
  }

  function frame(now) {
    const dt = Math.min(50, now - lastT); lastT = now;
    const tg = target();
    const k = 1 - Math.pow(0.0015, dt / 1000); // независимо от FPS, ~0.2 s сглаживание
    for (const key of KEYS) cur[key] += (tg[key] - cur[key]) * k;

    // моргание
    if (blinkPhase > 0) {
      blinkPhase -= dt;
      blink = Math.sin(Math.max(0, blinkPhase) / 140 * Math.PI);
      if (blinkPhase <= 0) { blink = 0; scheduleBlink(); }
    }

    // взгляд: приоритет — то, на что смотрит камера (рука/движение), затем состояние, затем блуждание
    const gazeOn = gaze && now < gaze.until && activity !== "learning";
    const lk = gazeOn ? [gaze.x * 15, gaze.y * 10] : tg.look;
    if (lk) { look.tx = lk[0]; look.ty = lk[1]; }
    const lookK = gazeOn ? Math.min(1, k * 2.2) : k; // быстрее следит за движением, как живой
    look.x += (look.tx - look.x) * lookK; look.y += (look.ty - look.y) * lookK;
    const wantPupil = tg.name === "surprised" || tg.name === "listening" || gazeOn ? 1.18 : tg.name === "angry" ? .88 : 1;
    pupilScale += (wantPupil - pupilScale) * k;

    // рот при речи: огибающая по времени + всплески на границах слов
    let mouth = { ...cur };
    if (talking) {
      const t = now / 1000;
      const base = 0.35 + 0.25 * Math.sin(t * 11) + 0.15 * Math.sin(t * 17.3 + 1.2);
      const pulse = now < pulseUntil ? 0.3 : 0;
      talkTarget = Math.max(0.08, Math.min(1, base + pulse + (energy - 50) / 400));
      talkLevel += (talkTarget - talkLevel) * (1 - Math.pow(0.0001, dt / 1000));
      mouth.open = talkLevel; mouth.width = 46 + talkLevel * 10;
    } else { talkLevel *= 0.6; }
    if (mouthOverride !== null) mouth.open = mouthOverride;

    const p = path(mouth);
    mouthEl.setAttribute("d", p.d);
    mouthEl.style.fill = p.fill ? `rgba(6,16,46,${p.fill})` : "none";

    for (const [g, side] of [[eyeL, -1], [eyeR, 1]]) {
      const x = side < 0 ? 120 : 280;
      const breathe = 1 + Math.sin(now / 1900) * 0.012; // лёгкое «дыхание»
      g.setAttribute("transform", `translate(${x} ${110 + Math.sin(now / 1900) * 1.2}) scale(${cur.scale * breathe})`);
    }
    lidL.setAttribute("d", lidPath(cur.lidIn, cur.lidOut, blink, -1));
    lidR.setAttribute("d", lidPath(cur.lidIn, cur.lidOut, blink, 1));
    const px = `translate(${look.x} ${look.y}) scale(${pupilScale})`;
    pupilL.setAttribute("transform", px); pupilR.setAttribute("transform", px);

    const by = cur.browY, tl = cur.browTilt;
    browL.setAttribute("d", `M80 ${50 + by + tl} Q120 ${40 + by} 160 ${50 + by - tl}`);
    browR.setAttribute("d", `M240 ${50 + by - tl} Q280 ${40 + by} 320 ${50 + by + tl}`);
    requestAnimationFrame(frame);
  }

  function scheduleBlink() { setTimeout(() => { blinkPhase = 140; }, 2200 + Math.random() * 3800); }
  function wander() {
    const gazeOn = gaze && performance.now() < gaze.until;
    if (!target().look && !gazeOn) { look.tx = (Math.random() - .5) * 18; look.ty = (Math.random() - .5) * 10; }
    if (Math.random() < .18) blinkPhase = blinkPhase > 0 ? blinkPhase : 140;  // иногда двойное моргание
    setTimeout(wander, 700 + Math.random() * 2000);
  }
  /** Смотреть в точку: x,y в диапазоне -1..1 (x>0 — вправо на экране). Через ttl мс взгляд возвращается к обычному. */
  function lookAt(x, y, ttl = 1500) { gaze = { x: Math.max(-1, Math.min(1, x)), y: Math.max(-1, Math.min(1, y)), until: performance.now() + ttl }; }
  /** Короткая реакция (удивление при резком движении), не перебивает речь и мысли. */
  function flash(name, ms = 700) { if (T[name]) { flashEmotion = name; flashUntil = performance.now() + ms; } }
  function setLearning(on) { svg.classList.toggle("learning", !!on); activity = on ? "learning" : "idle"; if (on) stopTalking(); }

  function setEmotion(e) { emotion = T[e] ? e : "neutral"; }
  function setActivity(a) {
    if (activity === "learning" && a !== "learning") return; // пока Woody учится, глаза закрыты (выход — setLearning(false))
    activity = a || "idle"; if (activity !== "speaking") stopTalking();
  }
  /** Принимает {emotion, activity, energy} от сервера. */
  function applyState(s) {
    if (!s) return;
    if (s.emotion) setEmotion(s.emotion);
    if (typeof s.energy === "number") energy = s.energy;
    if (s.activity) { setActivity(s.activity); if (s.activity === "speaking" && !talking) { /* рот двигает TTS */ } }
  }
  function startTalking() { if (activity === "learning") return; talking = true; activity = "speaking"; }
  function stopTalking() { talking = false; if (activity === "speaking") activity = "idle"; }
  function wordBoundary() { pulseUntil = performance.now() + 120; }
  /** Для будущего lip-sync по реальной громкости: 0..1 или null. */
  function setMouthLevel(v) { mouthOverride = v; }

  scheduleBlink(); setTimeout(wander, 1500); requestAnimationFrame(frame);
  return { applyState, setEmotion, setActivity, lookAt, flash, setLearning, startTalking, stopTalking, wordBoundary, setMouthLevel, states: Object.keys(T),
           get emotion() { return emotion; }, get activity() { return activity; }, get talking() { return talking; } };
})();
