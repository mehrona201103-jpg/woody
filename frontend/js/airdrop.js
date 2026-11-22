/* Приглашение «как AirDrop». Woody видит 👍/👆 → ищет устройства рядом → отправляет ссылку-приглашение.
   Честно о возможностях: сайт не может сам «стучаться» в чужой Bluetooth. Отправку делает системное меню «Поделиться»
   (AirDrop на iPhone, Nearby Share/Bluetooth на Android) — Woody открывает его с готовой ссылкой, а QR — запасной путь. */
const Invite = (() => {
  const $ = (id) => document.getElementById(id);
  const root = $("airdrop"), text = $("ad-text"), peer = $("ad-peer"), bubble = $("ad-bubble");
  const btnSend = $("ad-send"), btnQr = $("ad-qr");
  let data = null, open = false;

  const say = (t) => { text.textContent = t; };
  function close() { root.classList.add("hidden"); open = false; peer.classList.add("hidden"); bubble.classList.add("hidden"); bubble.classList.remove("fly"); btnSend.classList.add("hidden"); btnQr.classList.add("hidden"); }
  $("ad-close").onclick = close;

  async function start() {
    if (open) return;
    open = true; data = null;
    root.classList.remove("hidden"); peer.classList.add("hidden"); bubble.classList.add("hidden");
    btnSend.classList.add("hidden"); btnQr.classList.add("hidden");
    say("Ищу устройства рядом…");
    if (navigator.vibrate) navigator.vibrate(30);
    try {
      if (!(await Permissions.ensure("share", "Woody хочет отправить приглашение на устройство рядом.", "two"))) { say("Отправка приглашений запрещена в настройках."); return; }
      data = await Api.request("/transfer/create", { method: "POST" });
    } catch (e) { console.error("[Woody invite]", e); say(e.userMessage || "Не удалось создать приглашение."); return; }
    await new Promise((r) => setTimeout(r, 1800));          // радар «ищет»
    if (!open) return;
    peer.classList.remove("hidden"); say("Нашёл устройство рядом");
    if (navigator.vibrate) navigator.vibrate([20, 40, 20]);
    btnSend.classList.remove("hidden"); btnQr.classList.remove("hidden");
    btnSend.textContent = navigator.share ? "Отправить приглашение" : "Скопировать ссылку";
  }

  function flyBubble() {
    bubble.classList.remove("hidden", "fly"); void bubble.offsetWidth; bubble.classList.add("fly");
  }

  btnSend.onclick = async () => {
    if (!data) return;
    const url = data.join_url;
    try {
      if (navigator.share) {
        flyBubble();   // пузырь Woody летит к устройству, как в AirDrop
        await navigator.share({ title: "Woody", text: "Привет! Я Woody. Давай знакомиться:", url });
        say("Приглашение отправлено ✓");
      } else {
        await navigator.clipboard.writeText(url); flyBubble(); say("Ссылка скопирована — отправь её по Bluetooth или в мессенджере");
      }
      btnSend.classList.add("hidden");
      setTimeout(close, 2600);
    } catch (e) {
      if (e && e.name === "AbortError") { say("Отправка отменена"); return; }
      console.error("[Woody share]", e); say("Не получилось открыть отправку — покажи QR");
    }
  };

  btnQr.onclick = async () => { close(); try { await Transfer.show(); } catch (e) { Chat.add("woody", e.message, true); } };

  return { start, close, get open() { return open; } };
})();
