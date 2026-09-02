/* =============================================================
   JARVIS — Markdown Renderer (Fase 5)
   Renderização segura: todo texto é escapado; apenas tags fixas da
   whitelist são emitidas; links validados (apenas http/https).
   Nunca insere HTML não sanitizado via innerHTML.
   ============================================================= */
(function () {
  "use strict";

  const ESC_HTML = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  };

  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => ESC_HTML[c]);
  }

  /* Aplicado sobre texto já escapado. Precedência: código > negrito >
     itálico > tachado > link > auto-link. */
  const INLINE =
    /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*)|(~~[^~\n]+~~)|(\[([^\[\]\n]*)\]\((https?:\/\/[^\s)\n]+)\))|(https?:\/\/[^\s<>\n]+)/g;

  function sanitizeHref(h) {
    try {
      const url = new URL(h);
      if (url.protocol === "http:" || url.protocol === "https:") return url.href;
    } catch (_) {}
    return null;
  }

  function renderInline(text) {
    return text.replace(
      INLINE,
      (m, code, strong, em, del, linkFull, linkText, linkUrl, auto) => {
        if (code) return '<code class="md-code-inline">' + code.slice(1, -1) + "</code>";
        if (strong) return "<strong>" + strong.slice(2, -2) + "</strong>";
        if (em) return "<em>" + em.slice(1, -1) + "</em>";
        if (del) return "<s>" + del.slice(2, -2) + "</s>";
        if (linkFull) {
          const href = sanitizeHref(linkUrl);
          return href
            ? '<a class="md-link" href="' + href + '" rel="noopener noreferrer" target="_blank">' + linkText + "</a>"
            : m;
        }
        if (auto) {
          const href = sanitizeHref(auto);
          return href
            ? '<a class="md-link" href="' + href + '" rel="noopener noreferrer" target="_blank">' + auto + "</a>"
            : m;
        }
        return m;
      }
    );
  }

  function renderToHTML(md) {
    const lines = String(md).replace(/\r\n?/g, "\n").split("\n");
    const n = lines.length;
    let i = 0;
    let out = "";

    while (i < n) {
      const line = lines[i];

      // bloco de código delimitado (``` ou ~~~)
      const fence = line.match(/^\s*(`{3,}|~{3,})([a-zA-Z0-9_+-]*)\s*$/);
      if (fence) {
        const lang = fence[2];
        const buf = [];
        i++;
        while (i < n && !/^\s*(`{3,}|~{3,})\s*$/.test(lines[i])) {
          buf.push(lines[i]);
          i++;
        }
        i++; // fecha o bloco
        const code = esc(buf.join("\n"));
        const label = lang ? '<span class="md-pre-lang">' + esc(lang) + "</span>" : "";
        out +=
          '<div class="md-pre-wrap">' +
          label +
          '<pre class="md-pre"><code class="md-code">' +
          code +
          "</code></pre></div>";
        continue;
      }

      // headings h1-h4
      const h = line.match(/^(#{1,4})\s+(.*)$/);
      if (h) {
        const lvl = h[1].length;
        out +=
          "<h" + lvl + ' class="md-h md-h' + lvl + '">' + renderInline(esc(h[2])) + "</h" + lvl + ">";
        i++;
        continue;
      }

      // linha horizontal
      if (/^\s*(?:-{3,}|\*{3,})\s*$/.test(line)) {
        out += '<hr class="md-hr">';
        i++;
        continue;
      }

      // citação
      if (/^\s*>\s?/.test(line)) {
        const buf = [];
        while (i < n && /^\s*>\s?/.test(lines[i])) {
          buf.push(lines[i].replace(/^\s*>\s?/, ""));
          i++;
        }
        out += '<blockquote class="md-bq">' + renderInline(esc(buf.join("\n"))) + "</blockquote>";
        continue;
      }

      // listas (ordenada ou não, sem aninhamento profundo)
      const isOl = /^\s*\d+\.\s+/.test(line);
      const isUl = /^\s*[-*+]\s+/.test(line);
      if (isOl || isUl) {
        const items = [];
        while (i < n) {
          const l = lines[i];
          if (isOl && /^\s*\d+\.\s+/.test(l)) {
            items.push(l.replace(/^\s*\d+\.\s+/, ""));
            i++;
          } else if (isUl && /^\s*[-*+]\s+/.test(l)) {
            items.push(l.replace(/^\s*[-*+]\s+/, ""));
            i++;
          } else {
            break;
          }
        }
        const tag = isOl ? "ol" : "ul";
        out +=
          "<" + tag + ">" +
          items.map((t) => "<li>" + renderInline(esc(t)) + "</li>").join("") +
          "</" + tag + ">";
        continue;
      }

      // parágrafo (acumula até linha em branco ou próximo bloco)
      if (line.trim() !== "") {
        const buf = [];
        while (i < n) {
          const l = lines[i];
          if (l.trim() === "") break;
          if (/^\s*(?:#{1,4}\s|>\s?|[-*+]\s+|\d+\.\s+|`{3,}|~{3,}|-{3,}\s*$|\*{3,}\s*$)/.test(l)) break;
          buf.push(l);
          i++;
        }
        if (buf.length) {
          out += "<p>" + renderInline(esc(buf.join("\n"))) + "</p>";
          continue;
        }
      }
      i++;
    }
    return out;
  }

  function render(el, md) {
    el.innerHTML = renderToHTML(md);
  }

  const api = { renderToHTML, render };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof window !== "undefined") window.Markdown = api;
})();