/* Фото для Woody: загрузка из галереи и снимок камерой. Кадр сжимается и уходит только в этот чат — нигде не хранится. */
const Photos = (() => {
  const $ = (id) => document.getElementById(id);
  let dataUrl = null, stream = null;

  function shrink(source, w, h, max = 1280) {
    const k = Math.min(1, max / Math.max(w, h)), c = document.createElement("canvas");
    c.width = Math.round(w * k); c.height = Math.round(h * k);
    c.getContext("2d").drawImage(source, 0, 0, c.width, c.height);
    return c.toDataURL("image/jpeg", 0.82);
  }
  function fromFile(file) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file), img = new Image();
      img.onload = () => { const d = shrink(img, img.naturalWidth, img.naturalHeight); URL.revokeObjectURL(url); resolve(d); };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("Не удалось открыть изображение.")); };
      img.src = url;
    });
  }
  function set(d) { dataUrl = d; $("attach-img").src = d || ""; $("attach-prev").classList.toggle("hidden", !d); $("chat-input").dispatchEvent(new Event("input")); }
  function clear() { set(null); }

  async function picked(input) {
    const f = input.files && input.files[0]; input.value = "";
    if (!f) return;
    try { set(await fromFile(f)); $("chat-input").focus(); } catch (e) { Chat.add("woody", e.message, true); }
  }

  async function openLive() {
    const m = $("cam-modal"), v = $("cam-live"), err = $("cam-err");
    err.textContent = "";
    try {
      const ok = await Permissions.ensure("camera", "Woody хочет сделать снимок, чтобы увидеть и распознать то, что ты покажешь.", "two");
      if (!ok) return;
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
      v.srcObject = stream; m.classList.remove("hidden"); await v.play().catch(() => {});
    } catch (e) { console.error("[Woody photo cam]", e); closeLive(); Chat.add("woody", "Не удалось включить камеру. Можно загрузить фото из галереи.", true); }
  }
  function closeLive() { if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; } $("cam-live").srcObject = null; $("cam-modal").classList.add("hidden"); }

  $("btn-attach").onclick = () => $("file-pick").click();
  $("file-pick").onchange = (e) => picked(e.target);
  $("file-cam").onchange = (e) => picked(e.target);
  $("btn-camera").onclick = () => { if (matchMedia("(pointer:coarse)").matches) $("file-cam").click(); else openLive(); };
  $("cam-shot").onclick = () => { const v = $("cam-live"); if (v.videoWidth) { set(shrink(v, v.videoWidth, v.videoHeight)); closeLive(); $("chat-input").focus(); } };
  $("cam-close").onclick = closeLive;
  $("attach-x").onclick = clear;

  return { get image() { return dataUrl; }, clear };
})();
