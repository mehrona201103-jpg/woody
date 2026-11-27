/* Единый API-клиент. Все запросы идут на тот же origin с префиксом /api.
   Реальные ошибки пишутся в console; пользователю показываются понятные сообщения. */
const Api = (() => {
  const BASE = (window.WOODY_API_BASE || "").replace(/\/$/, "");
  const TOKEN_KEY = "woody_token";     // пароль владельца (WOODY_ACCESS_TOKEN)
  const SESSION_KEY = "woody_session"; // личная сессия пользователя

  class ApiError extends Error {
    constructor(status, code, message, userMessage) {
      super(message);
      this.status = status; this.code = code; this.userMessage = userMessage || message;
    }
  }

  const store = {
    get: (k) => { try { return localStorage.getItem(k); } catch (_) { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch (_) { /* приватный режим */ } },
    del: (k) => { try { localStorage.removeItem(k); } catch (_) { /* — */ } },
  };

  function parseDetail(body, fallback) {
    const d = body && body.detail;
    if (d && typeof d === "object") return { code: d.code || "error", message: d.message || fallback };
    if (typeof d === "string") return { code: "error", message: d };
    return { code: "error", message: fallback };
  }

  async function request(path, opts = {}) {
    const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    const token = store.get(TOKEN_KEY), session = store.get(SESSION_KEY);
    if (token) headers.Authorization = "Bearer " + token;
    if (session) headers["X-Woody-Session"] = session;
    const url = BASE + "/api" + path;
    let res;
    try {
      res = await fetch(url, { ...opts, headers });
    } catch (e) {
      if (e.name === "AbortError") throw e;
      console.error(`[Woody API] ${opts.method || "GET"} ${url} — сетевая ошибка:`, e);
      throw new ApiError(0, "network", String(e.message || e), "Не удалось подключиться к Woody.");
    }
    if (res.status === 401 && !path.startsWith("/auth/")) {
      // Сессия недействительна — возвращаем на экран входа (никаких системных окон).
      store.del(SESSION_KEY);
      window.dispatchEvent(new CustomEvent("woody:unauth"));
      throw new ApiError(401, "auth", "Нужно войти", "Войди, чтобы продолжить.");
    }
    let body = null;
    try { body = await res.json(); } catch (_) { /* не JSON */ }
    if (!res.ok) {
      const { code, message } = parseDetail(body, res.statusText);
      console.error(`[Woody API] ${opts.method || "GET"} ${url} → ${res.status} ${res.statusText}`, body);
      // Сырые «Not Found» пользователю не показываем.
      const friendly = res.status === 404 && code === "error" ? "Не удалось подключиться к Woody." : message;
      throw new ApiError(res.status, code, message, friendly);
    }
    return body;
  }

  /** Проверка, что по этому адресу отвечает именно Woody-backend. */
  async function health(timeoutMs = 6000) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetch(BASE + "/api/health", { signal: ctrl.signal, cache: "no-store" });
      if (!res.ok) { console.error(`[Woody API] GET /api/health → ${res.status} ${res.statusText}`); return false; }
      const data = await res.json();
      const ok = data && data.status === "ok" && data.service === "woody";
      if (!ok) console.error("[Woody API] /api/health вернул неожиданный ответ:", data);
      return ok;
    } catch (e) {
      console.error("[Woody API] /api/health недоступен:", e);
      return false;
    } finally { clearTimeout(timer); }
  }

  async function upload(path, formData, signal) {
    const headers = {};
    const token = store.get(TOKEN_KEY), session = store.get(SESSION_KEY);
    if (token) headers.Authorization = "Bearer " + token;
    if (session) headers["X-Woody-Session"] = session;
    let res;
    try { res = await fetch(BASE + "/api" + path, { method: "POST", headers, body: formData, signal }); }
    catch (e) { if (e.name === "AbortError") throw e; throw new ApiError(0, "network", String(e.message || e), "Нет связи с Woody."); }
    let data = {}; try { data = await res.json(); } catch (_) {}
    if (!res.ok) { const detail = data.detail; const message = (detail && typeof detail === "object" ? detail.message : detail) || "Не удалось обработать файл."; throw new ApiError(res.status, "error", message, message); }
    return data;
  }

  /** POST, ответ — бинарный (например аудио от /tts). Бросает ApiError при ошибке. */
  async function blob(path, body, signal) {
    const headers = { "Content-Type": "application/json" };
    const token = store.get(TOKEN_KEY), session = store.get(SESSION_KEY);
    if (token) headers.Authorization = "Bearer " + token;
    if (session) headers["X-Woody-Session"] = session;
    let res;
    try { res = await fetch(BASE + "/api" + path, { method: "POST", headers, body: JSON.stringify(body), signal }); }
    catch (e) { if (e.name === "AbortError") throw e; throw new ApiError(0, "network", String(e.message || e), "Нет связи с Woody."); }
    if (!res.ok) throw new ApiError(res.status, "error", res.statusText, "Сервер не смог ответить.");
    return res.blob();
  }

  /** GET файла с авторизацией и мгновенное скачивание в браузере. */
  async function download(path, filename) {
    const headers = {};
    const token = store.get(TOKEN_KEY), session = store.get(SESSION_KEY);
    if (token) headers.Authorization = "Bearer " + token;
    if (session) headers["X-Woody-Session"] = session;
    const res = await fetch(BASE + "/api" + path, { headers });
    if (!res.ok) throw new ApiError(res.status, "error", res.statusText, "Файл не найден или устарел.");
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
  }

  return {
    request, blob, upload, download, health, ApiError,
    setSession: (s) => store.set(SESSION_KEY, s),
    clearSession: () => store.del(SESSION_KEY),
    hasSession: () => !!store.get(SESSION_KEY),
  };
})();
