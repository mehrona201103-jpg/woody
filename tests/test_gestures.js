// Запуск: node tests/test_gestures.js
const assert = require("assert");
const { classify, TransferDetector, InviteDetector, WaveDetector } = require("../frontend/js/gestures.js");

// Синтетическая рука, направленная вверх: запястье внизу (y=0.9). y растёт вниз.
function hand({ index = true, middle = true, ring = true, pinky = true, thumb = "out" } = {}) {
  const lm = Array.from({ length: 21 }, () => ({ x: 0.5, y: 0.9 }));
  lm[0] = { x: 0.5, y: 0.9 };
  const cols = { index: [5, 0.42], middle: [9, 0.5], ring: [13, 0.58], pinky: [17, 0.66] };
  const ext = { index, middle, ring, pinky };
  for (const [name, [base, x]] of Object.entries(cols)) {
    lm[base] = { x, y: 0.7 };                       // mcp
    if (ext[name]) { lm[base + 1] = { x, y: 0.55 }; lm[base + 2] = { x, y: 0.4 }; lm[base + 3] = { x, y: 0.3 }; }
    else { lm[base + 1] = { x, y: 0.62 }; lm[base + 2] = { x, y: 0.7 }; lm[base + 3] = { x, y: 0.78 }; } // загнут к ладони
  }
  lm[1] = { x: 0.4, y: 0.82 }; lm[2] = { x: 0.36, y: 0.75 };
  if (thumb === "out") { lm[3] = { x: 0.25, y: 0.68 }; lm[4] = { x: 0.15, y: 0.62 }; }
  else if (thumb === "up") { lm[3] = { x: 0.35, y: 0.6 }; lm[4] = { x: 0.34, y: 0.45 }; }
  else if (thumb === "down") { lm[3] = { x: 0.35, y: 0.9 }; lm[4] = { x: 0.34, y: 1.05 }; }
  else { lm[3] = { x: 0.42, y: 0.74 }; lm[4] = { x: 0.46, y: 0.74 }; } // прижат к ладони
  return lm;
}

// --- классификация
assert.strictEqual(classify(hand()), "OPEN_PALM");
assert.strictEqual(classify(hand({ index: false, middle: false, ring: false, pinky: false, thumb: "tucked" })), "FIST");
assert.strictEqual(classify(hand({ index: false, middle: false, ring: false, pinky: false, thumb: "up" })), "THUMBS_UP");
assert.strictEqual(classify(hand({ index: false, middle: false, ring: false, pinky: false, thumb: "down" })), "THUMBS_DOWN");
assert.strictEqual(classify(hand({ ring: false, pinky: false, thumb: "tucked" })), "PEACE");
assert.strictEqual(classify(null), null);
assert.strictEqual(classify([]), null);

// --- детектор: ладонь → стабильный кулак срабатывает один раз
function run(seq, det = new TransferDetector()) {
  const fired = [];
  for (const [g, t] of seq) if (det.update(g, t)) fired.push(t);
  return fired;
}
const frames = (g, from, to, step = 60) => { const out = []; for (let t = from; t <= to; t += step) out.push([g, t]); return out; };

// ладонь 500 мс, затем кулак 900 мс → ровно одно срабатывание
let fired = run([...frames("OPEN_PALM", 0, 500), ...frames("FIST", 560, 1500)]);
assert.strictEqual(fired.length, 1, "ладонь→кулак должен сработать один раз");

// случайный короткий кулак (300 мс) без ладони — не срабатывает
assert.deepStrictEqual(run(frames("FIST", 0, 300)), []);

// кулак БЕЗ предшествующей ладони, даже долгий, — не срабатывает
assert.deepStrictEqual(run(frames("FIST", 0, 3000)), []);

// ладонь, затем короткий кулак (300 мс) — не срабатывает
assert.deepStrictEqual(run([...frames("OPEN_PALM", 0, 500), ...frames("FIST", 560, 860)]), []);

// кулак, прерванный потерей руки, сбрасывается
assert.deepStrictEqual(run([...frames("OPEN_PALM", 0, 500), ...frames("FIST", 560, 900), [null, 960], ...frames("FIST", 1020, 1400)]), []);

// ладонь была слишком давно (> окна) — не срабатывает
assert.deepStrictEqual(run([...frames("OPEN_PALM", 0, 500), ...frames("FIST", 6000, 7000)]), []);

// cooldown: повторное «ладонь→кулак» сразу после срабатывания игнорируется, а позже — работает
const det = new TransferDetector();
const seq = [...frames("OPEN_PALM", 0, 500), ...frames("FIST", 560, 1500), ...frames("OPEN_PALM", 1560, 2200), ...frames("FIST", 2260, 3100)];
assert.strictEqual(run(seq, det).length, 1, "в период cooldown второе срабатывание запрещено");
const later = [...frames("OPEN_PALM", 4200, 4800), ...frames("FIST", 4860, 5700)];
assert.strictEqual(run(later, det).length, 1, "после cooldown жест снова работает");


// --- 👆 указательный вверх
assert.strictEqual(classify(hand({ middle: false, ring: false, pinky: false, thumb: "tucked" })), "POINT_UP");

// --- приглашение: 👍/👆 удерживается; мелькнувший кадр не срабатывает; повтор — только после паузы
{
  const inv = new InviteDetector();
  const fire = (seq) => { const out = []; for (const [g, t] of seq) if (inv.update(g, t)) out.push(t); return out; };
  assert.deepStrictEqual(fire(frames("THUMBS_UP", 0, 200)), []);               // слишком коротко
  assert.strictEqual(fire(frames("POINT_UP", 1000, 1900)).length, 1);          // держится → один раз
  assert.deepStrictEqual(fire(frames("THUMBS_UP", 2000, 3000)), []);           // cooldown
  assert.strictEqual(fire(frames("THUMBS_UP", 20000, 21000)).length, 1);       // после паузы снова можно
  const noisy = new InviteDetector(); let n = 0;
  for (let t = 0; t < 2000; t += 60) if (noisy.update(t % 240 === 0 ? "FIST" : "THUMBS_UP", t)) n++;
  assert.strictEqual(n, 0);                                                    // дрожащий жест не считается
}

// --- махание: ладонь с колебаниями запястья
{
  const w = new WaveDetector(); let fired = 0;
  for (let t = 0; t < 1400; t += 60) if (w.update("OPEN_PALM", 0.5 + 0.08 * Math.sin(t / 110), t)) fired++;
  assert.strictEqual(fired, 1);
  const still = new WaveDetector(); let f2 = 0;
  for (let t = 0; t < 3000; t += 60) if (still.update("OPEN_PALM", 0.5, t)) f2++;
  assert.strictEqual(f2, 0);                                                   // неподвижная ладонь — не махание
}

console.log("gestures: all tests passed");
