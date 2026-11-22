/* Самообучение: по команде «обучай самого себя» Woody закрывает глазки, над головой после бровей появляется эффект,
   а сервер изучает мир через AI-ключ и складывает знания в мозг, пока лимит ключа не закончится. */
const Learn = (() => {
  let timer = null, active = false;
  const REASONS = {
    limit: "Лимит ключа закончился. Всё, что выучил, сохранено в моём мозге — теперь отвечу сам.",
    done: "Я закончил учиться: тем на сегодня хватит.",
    stopped: "Хорошо, остановился. Выученное сохранено.",
    error: "Обучение прервалось из-за ошибки, но всё выученное уже в моём мозге.",
    no_key: "Мне не к чему подключиться: ключ AI не настроен.",
  };

  function begin() {
    if (active) return;
    active = true; Face.setLearning(true); Chat.setStatus("Учусь…");
    clearInterval(timer); timer = setInterval(poll, 3000); poll();
  }

  async function end(reason) {
    if (!active) return;
    active = false; clearInterval(timer); timer = null;
    Face.setLearning(false); Chat.setStatus("");
    if (reason && REASONS[reason]) { Chat.add("woody", REASONS[reason]); Face.flash("happy", 1500); await Voice.speak(REASONS[reason], { rate: 1, pitch: 1 }); }
  }

  async function poll() {
    try {
      const st = await Api.request("/learn/status");
      if (st.active) { Chat.setStatus(`Учусь… выучил ${st.learned}${st.topic ? " · " + st.topic : ""}`); }
      else if (active) await end(st.reason || "done");
    } catch (e) { console.error("[Woody learn]", e); }
  }

  /** При открытии страницы: если сервер всё ещё учится, глаза снова закрываются. */
  async function resume() {
    try { const st = await Api.request("/learn/status"); if (st.active) begin(); } catch (_) { /* — */ }
  }

  async function stop() { try { await Api.request("/learn/stop", { method: "POST" }); } catch (_) { /* — */ } }

  return { begin, end, resume, stop, get active() { return active; } };
})();
