/* Woody Hand Transfer: QR со ссылкой /join/{token} и страница присоединения нового человека. */
const Transfer = (() => {
  const dlg = document.getElementById("transfer");
  const qrBox = document.getElementById("transfer-qr");
  const linkBox = document.getElementById("transfer-link");
  const timer = document.getElementById("transfer-timer");
  let tick = null, qrLib = null;

  function loadQrLib() {
    if (window.QRCode) return Promise.resolve(true);
    if (qrLib) return qrLib;
    qrLib = new Promise((resolve) => {
      const s = document.createElement("script");
      s.src = "https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js";
      s.crossOrigin = "anonymous"; s.onload = () => resolve(true);
      s.onerror = () => { console.error("[Woody] qrcode.js не загрузился"); resolve(false); };
      document.head.appendChild(s);
    });
    return qrLib;
  }

  function close() { clearInterval(tick); dlg.classList.add("hidden"); qrBox.innerHTML = ""; }
  document.getElementById("transfer-close").onclick = close;

  /** Создаёт токен на сервере и показывает QR. Возвращает true, если QR показан. */
  async function show() {
    let data;
    try { data = await Api.request("/transfer/create", { method: "POST" }); }
    catch (e) { console.error("[Woody transfer]", e); throw new Error(e.userMessage || "Не удалось создать ссылку передачи."); }

    qrBox.innerHTML = ""; linkBox.textContent = data.join_url;
    dlg.classList.remove("hidden");
    const hasQr = await loadQrLib();
    if (hasQr) new window.QRCode(qrBox, { text: data.join_url, width: 200, height: 200, colorDark: "#04122e", colorLight: "#ffffff", correctLevel: window.QRCode.CorrectLevel.M });
    else qrBox.textContent = "QR сейчас недоступен — откройте ссылку ниже на втором телефоне.";

    const end = Date.parse(data.expires_at);
    clearInterval(tick);
    const update = () => {
      const left = Math.max(0, Math.round((end - Date.now()) / 1000));
      timer.textContent = left ? `Ссылка действует ещё ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}` : "Ссылка Woody истекла.";
      if (!left) clearInterval(tick);
    };
    update(); tick = setInterval(update, 1000);
    return true;
  }

  /** Страница /join/{token}. Возвращает true, если пользователь создан и можно перейти на главную. */
  async function join(token, ui) {
    try { await Api.request("/transfer/check", { method: "POST", body: JSON.stringify({ token }) }); }
    catch (e) { ui.error(e.userMessage || "Не удалось подключиться к Woody."); return false; }
    await ui.greet(["Привет! Я Woody 👋", "Мы ещё не знакомы.", "Как тебя зовут?"]);
    const name = await ui.askName();
    try {
      const res = await Api.request("/transfer/redeem", { method: "POST", body: JSON.stringify({ token, name }) });
      Api.setSession(res.session);
      await ui.say(res.greeting);
      return true;
    } catch (e) { ui.error(e.userMessage || "Не удалось подключиться к Woody."); return false; }
  }

  return { show, close, join };
})();
