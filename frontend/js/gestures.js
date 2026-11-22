/* Классификация жестов по 21 точке руки (MediaPipe Hands) и детектор «ладонь → кулак».
   Чистая логика без DOM — тестируется в Node: node tests/test_gestures.js */
const Gestures = (() => {
  const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
  // Индексы: 0 запястье; 1-4 большой; 5-8 указательный; 9-12 средний; 13-16 безымянный; 17-20 мизинец (…, pip, dip, tip)
  const FINGERS = { index: [5, 6, 8], middle: [9, 10, 12], ring: [13, 14, 16], pinky: [17, 18, 20] };

  function fingerExtended(lm, [, pip, tip]) {
    return dist(lm[tip], lm[0]) > dist(lm[pip], lm[0]) * 1.05;
  }

  /** Возвращает "OPEN_PALM" | "FIST" | "POINT_UP" | "THUMBS_UP" | "THUMBS_DOWN" | "PEACE" | null. Эвристика: пороги нужно проверять на реальной руке. */
  function classify(lm) {
    if (!lm || lm.length < 21) return null;
    const palm = dist(lm[0], lm[9]) || 1e-6;
    const ext = {};
    for (const [name, idx] of Object.entries(FINGERS)) ext[name] = fingerExtended(lm, idx);
    const four = Object.values(ext).filter(Boolean).length;
    const thumbOut = dist(lm[4], lm[5]) > palm * 0.75;

    if (four === 4) return "OPEN_PALM";
    if (ext.index && ext.middle && !ext.ring && !ext.pinky) return "PEACE";
    // 👆 один указательный палец вверх
    if (ext.index && !ext.middle && !ext.ring && !ext.pinky && !thumbOut && lm[8].y < lm[5].y - palm * 0.5) return "POINT_UP";
    if (four === 0) {
      if (!thumbOut) return "FIST";
      if (lm[4].y < lm[2].y - palm * 0.3) return "THUMBS_UP";   // ось y направлена вниз
      if (lm[4].y > lm[2].y + palm * 0.3) return "THUMBS_DOWN";
    }
    return null;
  }

  /** Срабатывает на OPEN_PALM → FIST, если кулак держится стабильно. Случайный короткий кулак игнорируется. */
  class TransferDetector {
    constructor(opts = {}) {
      this.palmMinMs = opts.palmMinMs ?? 300;      // ладонь должна быть видна не меньше
      this.palmWindowMs = opts.palmWindowMs ?? 3000; // кулак должен начаться не позже, чем через столько после ладони
      this.fistHoldMs = opts.fistHoldMs ?? 600;    // удержание кулака (500–800 мс)
      this.minFistFrames = opts.minFistFrames ?? 6;  // и минимум кадров подряд
      this.cooldownMs = opts.cooldownMs ?? 2500;
      this.reset(); this.cooldownUntil = 0;
    }
    reset() { this.palmSince = null; this.lastPalmStable = null; this.fistSince = null; this.fistFrames = 0; }
    update(gesture, now) {
      if (now < this.cooldownUntil) { this.reset(); return false; }
      if (gesture === "OPEN_PALM") {
        if (this.palmSince === null) this.palmSince = now;
        if (now - this.palmSince >= this.palmMinMs) this.lastPalmStable = now;
        this.fistSince = null; this.fistFrames = 0;
        return false;
      }
      if (gesture === "FIST") {
        if (this.fistSince === null) { this.fistSince = now; this.fistFrames = 0; }
        this.fistFrames++;
        const armed = this.lastPalmStable !== null && this.fistSince - this.lastPalmStable <= this.palmWindowMs;
        if (armed && now - this.fistSince >= this.fistHoldMs && this.fistFrames >= this.minFistFrames) {
          this.cooldownUntil = now + this.cooldownMs;
          this.reset();
          return true;
        }
        return false;
      }
      // другой жест / рука потеряна: сбрасываем текущие серии, но «ладонь недавно была» помним до истечения окна
      this.palmSince = null; this.fistSince = null; this.fistFrames = 0;
      return false;
    }
  }

  /** Приглашение: 👍 или 👆 удерживается ~0,7 с (не случайный кадр). */
  class InviteDetector {
    constructor(opts = {}) {
      this.holdMs = opts.holdMs ?? 700; this.minFrames = opts.minFrames ?? 6; this.cooldownMs = opts.cooldownMs ?? 8000;
      this.cooldownUntil = 0; this.reset();
    }
    reset() { this.since = null; this.frames = 0; }
    update(gesture, now) {
      if (now < this.cooldownUntil) { this.reset(); return false; }
      if (gesture !== "THUMBS_UP" && gesture !== "POINT_UP") { this.reset(); return false; }
      if (this.since === null) { this.since = now; this.frames = 0; }
      this.frames++;
      if (now - this.since >= this.holdMs && this.frames >= this.minFrames) { this.cooldownUntil = now + this.cooldownMs; this.reset(); return true; }
      return false;
    }
  }

  /** Приветственное махание: открытая ладонь, запястье несколько раз меняет направление. */
  class WaveDetector {
    constructor(opts = {}) { this.windowMs = opts.windowMs ?? 1400; this.minTurns = opts.minTurns ?? 3; this.minSwing = opts.minSwing ?? 0.035; this.cooldownMs = opts.cooldownMs ?? 7000; this.cooldownUntil = 0; this.pts = []; }
    update(gesture, wristX, now) {
      if (now < this.cooldownUntil || gesture !== "OPEN_PALM") { if (gesture !== "OPEN_PALM") this.pts = []; return false; }
      this.pts.push([now, wristX]);
      this.pts = this.pts.filter(([t]) => now - t <= this.windowMs);
      let turns = 0, dir = 0, anchor = this.pts[0][1];
      for (const [, x] of this.pts) {
        const d = x - anchor;
        if (Math.abs(d) >= this.minSwing) { const nd = Math.sign(d); if (dir && nd !== dir) turns++; dir = nd; anchor = x; }
      }
      if (turns >= this.minTurns) { this.cooldownUntil = now + this.cooldownMs; this.pts = []; return true; }
      return false;
    }
  }

  return { classify, TransferDetector, InviteDetector, WaveDetector };
})();

if (typeof module !== "undefined") module.exports = Gestures;
