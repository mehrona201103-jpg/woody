/* Возможности устройства. Woody делает на устройстве только то, что разрешено в Permissions (хранятся на сервере, у каждого свои).
   Если разрешено — действует сразу, без лишних вопросов; если не разрешено — спросит один раз; если запрещено — честно скажет, что не может.
   Браузер не даёт сайтам управлять системой (файлы, SMS, настройки) — этого здесь нет и быть не может. */
const Device = (() => {
  const hooks = { camera: null, invite: null };
  const DENIED = (what) => `Мне не разрешено ${what}. Включить можно в настройках, в разделе «Разрешения».`;

  async function can(action, label) {
    try { return await Permissions.ensure(action, label, "two"); } catch (e) { console.error("[Woody permissions]", e); return false; }
  }

  const fmtDate = (d) => d.toLocaleString("ru-RU", { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });

  const intents = [
    { re: /(который час|сколько времени|какое сегодня число|какая сегодня дата|какой сегодня день)/i,
      run: async () => ({ reply: `Сейчас ${fmtDate(new Date())}.` }) },

    { re: /(заряд\w*\s+(батаре|телефон|устройств)|уровень заряда|сколько (?:у меня |у тебя )?(?:заряда|процентов)|батаре[яиюе])/i,
      run: async () => {
        if (!(await can("device.info", "Woody хочет узнать состояние устройства (заряд батареи, сеть)."))) return { reply: DENIED("смотреть состояние устройства") };
        if (!navigator.getBattery) return { reply: "Мой браузер не показывает заряд батареи." };
        const b = await navigator.getBattery();
        return { reply: `Заряд ${Math.round(b.level * 100)} процентов${b.charging ? ", телефон на зарядке" : ""}.` };
      } },

    { re: /(где я\b|мо[её] местоположение|мои координаты|где мы находимся)/i,
      run: async () => {
        if (!(await can("location", "Woody хочет узнать, где ты находишься (геолокация)."))) return { reply: DENIED("узнавать геолокацию") };
        if (!navigator.geolocation) return { reply: "Геолокация недоступна в этом браузере." };
        const pos = await new Promise((res, rej) => navigator.geolocation.getCurrentPosition(res, rej, { enableHighAccuracy: true, timeout: 12000 }))
          .catch((e) => { console.error("[Woody geo]", e); return null; });
        if (!pos) return { reply: "Не получилось определить место: браузер не дал геолокацию." };
        const { latitude: la, longitude: lo, accuracy } = pos.coords;
        return { reply: `Ты примерно здесь: ${la.toFixed(4)}, ${lo.toFixed(4)}, точность около ${Math.round(accuracy)} метров.` };
      } },

    { re: /(?:включи|сделай|запусти|дай)\s+вибраци|завибрируй/i,
      run: async () => {
        if (!navigator.vibrate) return { reply: "Этот браузер не умеет вибрировать." };
        navigator.vibrate([200, 100, 200]);
        return { reply: "Бззз!" };
      } },

    { re: /(что у меня в буфере|прочитай буфер|что в буфере обмена)/i,
      run: async () => {
        if (!(await can("clipboard", "Woody хочет работать с буфером обмена."))) return { reply: DENIED("работать с буфером обмена") };
        try { const t = await navigator.clipboard.readText(); return { reply: t ? `В буфере: ${t.slice(0, 300)}` : "Буфер пуст." }; }
        catch (e) { console.error("[Woody clipboard]", e); return { reply: "Браузер не дал прочитать буфер обмена." }; }
      } },

    { re: /^\s*(?:скопируй|копируй)\s*[:\-—]?\s*(.+)$/i,
      run: async (m) => {
        if (!(await can("clipboard", "Woody хочет работать с буфером обмена."))) return { reply: DENIED("работать с буфером обмена") };
        try { await navigator.clipboard.writeText(m[1].trim()); return { reply: "Скопировал." }; }
        catch (e) { console.error("[Woody clipboard]", e); return { reply: "Браузер не дал записать в буфер обмена." }; }
      } },

    { re: /(?:напомни|уведоми)(.*?)\s+через\s+(\d+)\s*(секунд\w*|сек|минут\w*|мин|час\w*)/i,
      run: async (m) => {
        if (!(await can("notifications", "Woody хочет показывать уведомления."))) return { reply: DENIED("показывать уведомления") };
        if (!("Notification" in window)) return { reply: "Уведомления недоступны в этом браузере." };
        if (Notification.permission !== "granted" && (await Notification.requestPermission()) !== "granted") return { reply: "Браузер не дал показывать уведомления." };
        const n = parseInt(m[2], 10), u = m[3].toLowerCase();
        const ms = n * (u.startsWith("сек") ? 1000 : u.startsWith("час") ? 3600000 : 60000);
        const what = (m[1] || "").replace(/^(мне|меня|о|про|что)\s+/i, "").trim() || "Время пришло";
        setTimeout(() => { try { new Notification("Woody", { body: what }); } catch (_) { /* — */ } }, ms);
        return { reply: `Хорошо, напомню через ${n} ${m[3]}. Только держи Woody открытым: браузер не даёт страницам работать после закрытия.` };
      } },

    { re: /(сфотографируй|сделай (?:фото|снимок|селфи))/i,
      run: async () => {
        const img = HandTracker.snapshot();
        if (!img) return { reply: "Камера сейчас выключена. Включи её кнопкой 👁 или скажи «включи камеру»." };
        return { reply: "Готово, снимок только у тебя на экране — я его нигде не сохраняю.", image: img };
      } },

    { re: /(что ты видишь|что видишь|что на камере|посмотри на меня|кто передо мной|опиши.{0,12}(видишь|кадр))/i,
      run: async (m, text) => {
        const img = HandTracker.snapshot();
        if (!img) return { reply: "Камера сейчас выключена. Включи её кнопкой 👁, и я посмотрю." };
        const res = await Api.request("/see", { method: "POST", body: JSON.stringify({ image: img, question: text.slice(0, 280) }) });
        return { reply: res.reply };
      } },

    { re: /(включи|запусти|открой).{0,12}камер/i,
      run: async () => { if (hooks.camera) await hooks.camera(true); return { reply: HandTracker.active ? "Камера включена, я вижу тебя." : "Не получилось включить камеру." }; } },
    { re: /(выключи|отключи|закрой).{0,12}камер/i,
      run: async () => { if (hooks.camera) await hooks.camera(false); return { reply: "Камера выключена." }; } },

    { re: /(пригласи|отправь приглашение|поделись.{0,10}(вуди|woody)|передай (?:мне )?(вуди|woody) (?:другу|другому))/i,
      run: async () => { if (hooks.invite) hooks.invite(); return { reply: "Ищу устройства рядом, чтобы отправить приглашение." }; } },
  ];

  /** Возвращает {reply, image?} если запрос выполнен на устройстве, иначе null (тогда отвечает Woody Core). */
  async function handle(text) {
    for (const it of intents) {
      const m = text.match(it.re);
      if (!m) continue;
      try { return await it.run(m, text); }
      catch (e) { console.error("[Woody device]", e); return { reply: "Не получилось это сделать на устройстве." }; }
    }
    return null;
  }

  return { handle, hooks };
})();
