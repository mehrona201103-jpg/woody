/* Боковая панель: история чатов (как в ChatGPT) и групповые чаты. Woody состоит в каждой группе. */
const History = (() => {
  const $ = (id) => document.getElementById(id);
  const api = Api.request;
  let hooks = {}, chats = [];
  const X = '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>';

  function renderChats() {
    const box = $("chat-list"); box.innerHTML = "";
    if (!chats.length) { box.innerHTML = '<p class="side-empty">Пока пусто — напиши Woody.</p>'; return; }
    for (const c of chats) {
      const row = document.createElement("div"); row.className = "side-row" + (c.id === Chat.chatId ? " active" : "");
      const b = document.createElement("button"); b.type = "button"; b.className = "side-item"; b.textContent = c.title; b.onclick = () => hooks.openChat && hooks.openChat(c.id);
      const d = document.createElement("button"); d.type = "button"; d.className = "side-x"; d.setAttribute("aria-label", "Удалить чат"); d.innerHTML = X;
      d.onclick = async (e) => { e.stopPropagation(); if (hooks.confirmDelete && !(await hooks.confirmDelete("Удалить этот чат?"))) return; try { await api(`/chats/${c.id}`, { method: "DELETE" }); if (c.id === Chat.chatId) Chat.newChat(); refresh(); } catch (_) { /* — */ } };
      row.append(b, d); box.append(row);
    }
  }
  async function refresh() { try { chats = await api("/chats"); renderChats(); } catch (_) { /* — */ } }
  return { init(h) { hooks = h; }, refresh, render: renderChats, reset() { chats = []; $("chat-list").innerHTML = ""; } };
})();

const Groups = (() => {
  const $ = (id) => document.getElementById(id);
  const api = Api.request;
  let list = [], cur = null, members = {}, me = 0, lastId = 0, timer = 0, ticks = 0, seen = new Set(), sending = false, hooks = {};
  const log = $("grp-log"), scroller = $("grp-scroll");

  // ---------- маленькое окно с полем ввода
  function ask({ title, placeholder, ok, maxlength = 60, submit }) {
    return new Promise((resolve) => {
      const m = $("ask"), inp = $("ask-input"), err = $("ask-error"), okb = $("ask-ok");
      $("ask-title").textContent = title; inp.placeholder = placeholder || ""; inp.maxLength = maxlength; inp.value = ""; err.textContent = ""; okb.textContent = ok || "OK";
      m.classList.remove("hidden"); setTimeout(() => inp.focus(), 60);
      const close = (v) => { m.classList.add("hidden"); okb.onclick = $("ask-cancel").onclick = inp.onkeydown = null; resolve(v); };
      const go = async () => {
        const v = inp.value.trim(); if (!v) return; okb.disabled = true; err.textContent = "";
        try { await submit(v); close(v); } catch (e) { err.textContent = e.userMessage || e.message || "Не получилось."; } finally { okb.disabled = false; }
      };
      okb.onclick = go; $("ask-cancel").onclick = () => close(null); inp.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); go(); } };
    });
  }

  // ---------- список групп в боковой панели
  function renderList() {
    const box = $("group-list"); box.innerHTML = "";
    if (!list.length) { box.innerHTML = '<p class="side-empty">Создай группу — как в WhatsApp. Woody будет внутри сразу.</p>'; return; }
    for (const g of list) {
      const b = document.createElement("button"); b.type = "button"; b.className = "side-row side-item grp" + (cur && cur.id === g.id ? " active" : "");
      const av = document.createElement("span"); av.className = "mini-av sm"; av.innerHTML = Avatars.woody();
      const col = document.createElement("span"); col.className = "grp-meta";
      const title = document.createElement("b"); title.textContent = g.name;
      const prev = document.createElement("small"); prev.textContent = g.last_text || "Нет сообщений";
      col.append(title, prev);
      const meta = document.createElement("span"); meta.className = "grp-side";
      if (g.members) { const c = document.createElement("em"); c.textContent = g.members + " уч."; meta.append(c); }
      b.append(av, col, meta);
      b.onclick = () => open(g.id); box.append(b);
    }
  }
  async function refresh() { try { list = await api("/groups"); renderList(); } catch (_) { /* — */ } }

  async function create() {
    let made = null;
    await ask({ title: "Название группы", placeholder: "Например: Семья", ok: "Создать", submit: async (v) => { made = await api("/groups", { method: "POST", body: JSON.stringify({ name: v }) }); } });
    if (made) { await refresh(); open(made.id); }
  }

  // ---------- экран группы
  function senderOf(id) { return id === null ? { name: "Woody", woody: true } : members[id] || { name: "Участник" }; }

  function addMsg(m) {
    if (seen.has(m.id)) return; seen.add(m.id); lastId = Math.max(lastId, m.id);
    const who = senderOf(m.user_id), mine = m.user_id === me;
    const row = document.createElement("div"); row.className = "msg grp-msg " + (mine ? "user" : "woody");
    if (!mine) { const av = document.createElement("div"); av.className = "mini-av"; av.innerHTML = who.woody ? Avatars.woody() : Avatars.user(who.name, who.picture); row.append(av); }
    const bub = document.createElement("div"); bub.className = "bub";
    if (!mine) { const n = document.createElement("div"); n.className = "who"; n.textContent = who.name; bub.append(n); }
    const body = document.createElement("div"); body.className = "rich";
    if (who.woody) body.innerHTML = Rich.render(m.text); else body.textContent = m.text;
    bub.append(body); row.append(bub); log.append(row);
  }
  const bottom = (smooth = true) => requestAnimationFrame(() => scroller.scrollTo({ top: scroller.scrollHeight, behavior: smooth ? "smooth" : "auto" }));

  function renderHeader(info) {
    members = {}; for (const u of info.members) members[u.id] = u;
    const count = 1 + (info.members ? info.members.length : 0);
    $("grp-name").textContent = info.name;
    const sub = document.getElementById("grp-sub");
    if (sub) sub.textContent = count + " участник" + (count === 1 ? "" : count < 5 ? "а" : "ов");
    const faces = ['<span class="st">' + Avatars.woody() + "</span>", ...info.members.slice(0, 4).map((u) => '<span class="st">' + Avatars.user(u.name, u.picture) + "</span>")];
    $("grp-avatars").innerHTML = faces.join("");
    $("grp-members").innerHTML = '<span class="chip">' + Avatars.woody() + "<b>Woody</b> <small>· бот</small></span>" +
      info.members.map((u) => '<span class="chip">' + Avatars.user(u.name, u.picture) + "<b>" + Avatars.esc(u.name) + "</b>" + (u.id === me ? " <small>· ты</small>" : "") + "</span>").join("");
  }

  async function open(id) {
    clearInterval(timer); seen = new Set(); lastId = 0; log.innerHTML = "";
    try {
      const info = await api(`/groups/${id}`); cur = { id, name: info.name }; me = info.me; renderHeader(info);
      for (const m of await api(`/groups/${id}/messages`)) addMsg(m);
    } catch (e) { console.error("[Woody group]", e); return; }
    renderList(); if (hooks.show) hooks.show(); bottom(false);
    ticks = 0; timer = setInterval(poll, 3000);
  }
  async function poll() {
    if (!cur) return;
    try {
      const fresh = await api(`/groups/${cur.id}/messages?after=${lastId}`);
      if (fresh.length) { for (const m of fresh) addMsg(m); bottom(); }
      if (++ticks % 5 === 0) renderHeader(await api(`/groups/${cur.id}`));
    } catch (_) { /* связь пропала — попробуем в следующий раз */ }
  }
  function close() { clearInterval(timer); timer = 0; cur = null; renderList(); }

  async function send(text) {
    if (sending || !cur || !text.trim()) return;
    sending = true;
    const dots = document.createElement("div"); dots.className = "msg woody"; dots.innerHTML = '<div class="mini-av">' + Avatars.woody() + '</div><div class="bub"><div class="typing"><i></i><i></i><i></i></div></div>';
    try {
      const gid = cur.id, p = api(`/groups/${gid}/messages`, { method: "POST", body: JSON.stringify({ text }) });
      log.append(dots); bottom();
      const msgs = await p; dots.remove(); for (const m of msgs) addMsg(m); bottom();
    } catch (e) { dots.remove(); const err = document.createElement("div"); err.className = "msg woody error"; err.innerHTML = '<div class="bub">' + Avatars.esc(e.userMessage || "Не удалось отправить.") + "</div>"; log.append(err); }
    finally { sending = false; }
  }

  async function addMember() {
    if (!cur) return;
    await ask({ title: "Имя человека в Woody", placeholder: "Имя участника", ok: "Добавить участника", maxlength: 50,
      submit: async (v) => { const r = await api(`/groups/${cur.id}/members`, { method: "POST", body: JSON.stringify({ name: v }) }); renderHeader({ name: cur.name, members: r.members }); } });
  }
  async function leave() { if (!cur) return; try { await api(`/groups/${cur.id}`, { method: "DELETE" }); } catch (_) { /* — */ } close(); await refresh(); }

  // ---------- кнопки
  $("grp-new").onclick = create;
  $("grp-add").onclick = addMember;
  $("grp-back").onclick = () => { close(); if (hooks.back) hooks.back(); };
  $("grp-leave").onclick = async () => { if (hooks.confirmDelete && !(await hooks.confirmDelete("Выйти из группы?"))) return; await leave(); if (hooks.back) hooks.back(); };
  const ta = $("grp-input"), sb = $("grp-send");
  ta.oninput = () => { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 140) + "px"; sb.disabled = !ta.value.trim(); };
  ta.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey && !matchMedia("(pointer:coarse)").matches) { e.preventDefault(); $("grp-form").requestSubmit(); } };
  $("grp-form").onsubmit = (e) => { e.preventDefault(); const t = ta.value.trim(); if (!t) return; ta.value = ""; ta.style.height = "auto"; sb.disabled = true; send(t); };

  return { init(h) { hooks = h; }, refresh, open, close, reset() { close(); list = []; renderList(); } };
})();
