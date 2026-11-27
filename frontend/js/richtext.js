/* Красивое оформление ответов: абзацы, списки, заголовки, **жирный**, `код` и блоки кода с подсветкой и кнопкой «Копировать». */
const Rich = (() => {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const KW = "def|class|return|if|elif|else|for|while|in|not|and|or|is|import|from|as|with|try|except|finally|raise|pass|break|continue|lambda|yield|async|await|None|True|False|self|print|function|const|let|var|new|this|null|undefined|true|false|switch|case|default|export|public|private|static|void|int|float|double|string|bool|char|struct|fn|use|mut|impl|match|go|func|package|type|interface|select|SELECT|FROM|WHERE|INSERT|UPDATE|DELETE|JOIN|CREATE|TABLE|echo|then|fi|do|done";
  const TOK = new RegExp(`(\\/\\/[^\\n]*|#[^\\n]*|\\/\\*[\\s\\S]*?\\*\\/)|("""[\\s\\S]*?"""|"(?:\\\\.|[^"\\\\\\n])*"|'(?:\\\\.|[^'\\\\\\n])*'|\`(?:\\\\.|[^\`\\\\])*\`)|\\b(${KW})\\b|\\b(\\d+(?:\\.\\d+)?)\\b|(\\b[A-Za-z_]\\w*)(?=\\()`, "g");
  const HASH = /^(python|py|bash|sh|shell|yaml|yml|ruby|rb|toml|r|)$/i;

  function highlight(code, lang) {
    const hash = HASH.test(lang || "");
    let out = "", last = 0, m;
    TOK.lastIndex = 0;
    while ((m = TOK.exec(code))) {
      out += esc(code.slice(last, m.index));
      const t = m[0];
      if (m[1]) out += ((t[0] === "#" && !hash) || (t.startsWith("//") && hash)) ? esc(t) : `<i class="c">${esc(t)}</i>`;
      else if (m[2]) out += `<i class="s">${esc(t)}</i>`;
      else if (m[3]) out += `<i class="k">${esc(t)}</i>`;
      else if (m[4]) out += `<i class="n">${esc(t)}</i>`;
      else out += `<i class="f">${esc(t)}</i>`;
      last = m.index + t.length;
    }
    return out + esc(code.slice(last));
  }

  function inline(t) {
    return esc(t)
      .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
      .replace(/`([^`\n]+)`/g, '<code class="ic">$1</code>')
      .replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>")
      .replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,!?:;]|$)/g, "$1<em>$2</em>");
  }

  function block(text) {
    const lines = text.split("\n"), out = [];
    let list = null, para = [];
    const flushP = () => { if (para.length) { out.push("<p>" + para.map(inline).join("<br>") + "</p>"); para = []; } };
    const flushL = () => { if (list) { out.push(`<${list.t}>` + list.items.map((i) => "<li>" + inline(i) + "</li>").join("") + `</${list.t}>`); list = null; } };
    for (const raw of lines) {
      const line = raw.trimEnd(); let m;
      if (!line.trim()) { flushP(); flushL(); continue; }
      if ((m = line.match(/^(#{1,4})\s+(.*)/))) { flushP(); flushL(); out.push(`<h${m[1].length + 2} class="rh">${inline(m[2])}</h${m[1].length + 2}>`); continue; }
      if ((m = line.match(/^\s*[-*•]\s+(.*)/))) { flushP(); if (!list || list.t !== "ul") { flushL(); list = { t: "ul", items: [] }; } list.items.push(m[1]); continue; }
      if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) { flushP(); if (!list || list.t !== "ol") { flushL(); list = { t: "ol", items: [] }; } list.items.push(m[1]); continue; }
      if (/^[-_]{3,}$/.test(line.trim())) { flushP(); flushL(); out.push("<hr>"); continue; }
      flushL(); para.push(line);
    }
    flushP(); flushL();
    return out.join("");
  }

  const COPY = '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>';

  /** Возвращает HTML; весь текст экранируется. */
  function render(text) {
    let html = "", last = 0, m;
    const re = /```([\w+#.-]*)[ \t]*\n?([\s\S]*?)(?:```|$)/g;
    while ((m = re.exec(text))) {
      html += block(text.slice(last, m.index));
      const lang = (m[1] || "").toLowerCase(), code = m[2].replace(/\n$/, "");
      html += `<div class="code"><div class="code-head"><span>${esc(lang || "код")}</span><button type="button" class="copy-code" aria-label="Скопировать код">${COPY}<b>Копировать</b></button></div><pre><code>${highlight(code, lang)}</code></pre></div>`;
      last = re.lastIndex;
      if (m[0].length === 0) re.lastIndex++;
    }
    return html + block(text.slice(last));
  }

  async function copyText(t) {
    try { await navigator.clipboard.writeText(t); return true; } catch (_) { /* старые браузеры */ }
    const ta = Object.assign(document.createElement("textarea"), { value: t, style: "position:fixed;opacity:0" });
    document.body.append(ta); ta.select(); let ok = false;
    try { ok = document.execCommand("copy"); } catch (_) { /* — */ }
    ta.remove(); return ok;
  }
  function flash(btn, label) { const b = btn.querySelector("b"); const old = b ? b.textContent : ""; btn.classList.add("done"); if (b) b.textContent = label; setTimeout(() => { btn.classList.remove("done"); if (b) b.textContent = old; }, 1400); }

  // одна общая обработка нажатий «Копировать» у блоков кода
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest(".copy-code");
    if (!btn) return;
    const code = btn.closest(".code").querySelector("code").textContent;
    flash(btn, (await copyText(code)) ? "Скопировано" : "Не вышло");
  });

  return { render, copyText, flash, COPY };
})();
