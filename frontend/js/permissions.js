/* Запрос разрешений. Все решения хранятся на сервере (Permission Manager), отдельно для каждого пользователя. */
const Permissions = (() => {
  const api = (path, opts) => Api.request(path, opts);

  function choose(text, buttons) {
    return new Promise((resolve) => {
      const modal = document.getElementById("modal");
      document.getElementById("modal-text").textContent = text;
      const box = document.getElementById("modal-actions");
      box.innerHTML = "";
      for (const [label, value, cls] of buttons) {
        const b = document.createElement("button");
        b.textContent = label; if (cls) b.className = cls;
        b.onclick = () => { modal.classList.add("hidden"); resolve(value); };
        box.appendChild(b);
      }
      modal.classList.remove("hidden");
    });
  }

  /** true — действие разрешено сейчас. mode "two": только «Разрешить»/«Запретить» (разрешить = всегда). */
  async function ensure(action, label, mode = "three") {
    let { status } = await api(`/permissions/${action}/check`, { method: "POST" });
    if (status === "allowed") return true;
    if (status === "denied") return false;
    const buttons = mode === "two"
      ? [["Разрешить", "ALLOW_ALWAYS"], ["Запретить", "DENY", "danger"]]
      : [["Разрешить один раз", "ALLOW_ONCE"], ["Разрешить всегда", "ALLOW_ALWAYS"], ["Запретить", "DENY", "danger"]];
    const choice = await choose(label, buttons);
    await api(`/permissions/${action}`, { method: "PUT", body: JSON.stringify({ decision: choice }) });
    if (choice === "DENY") return false;
    ({ status } = await api(`/permissions/${action}/check`, { method: "POST" }));
    return status === "allowed";
  }

  return { api, ensure, choose };
})();
