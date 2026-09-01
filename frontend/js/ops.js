/* =============================================================
   JARVIS — Central de Operações (Fase 11)
   Aba do SPA que consome /api/ops/* (observabilidade sanitizada).
   Vanilla JS, sem dependências.
   ============================================================= */
"use strict";

const OpsView = (() => {
  const els = {
    view: document.getElementById("ops-view"),
    tabChat: document.getElementById("tab-chat"),
    tabOps: document.getElementById("tab-ops"),
    refresh: document.getElementById("ops-refresh"),
    aiBody: document.getElementById("ops-ai-body"),
    providersBody: document.getElementById("ops-providers-body"),
    systemBody: document.getElementById("ops-system-body"),
    contextBody: document.getElementById("ops-context-body"),
    memoryBody: document.getElementById("ops-memory-body"),
    toolsBody: document.getElementById("ops-tools-body"),
    tasksBody: document.getElementById("ops-tasks-body"),
    atlasBody: document.getElementById("ops-atlas-body"),
    eventsBody: document.getElementById("ops-events-body"),
  };

  const CHAT_ROOT = document.querySelector(".shell");
  const COMPOSER = document.getElementById("input-form");

  let open = false;

  /* ---------- navegação ---------- */
  function setView(showOps) {
    open = showOps;
    if (els.view) els.view.hidden = !showOps;
    if (CHAT_ROOT) CHAT_ROOT.hidden = showOps;
    if (COMPOSER) COMPOSER.hidden = showOps;
    if (els.tabChat) els.tabChat.classList.toggle("is-active", !showOps);
    if (els.tabOps) els.tabOps.classList.toggle("is-active", showOps);
    if (showOps && open) load();
  }

  function esc(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function time(iso) {
    if (!iso) return "—";
    try {
      return new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    } catch {
      return "—";
    }
  }

  /* ---------- renderizadores ---------- */
  function renderAI(core) {
    if (!els.aiBody) return;
    if (!core) {
      els.aiBody.innerHTML = '<span class="ops-muted">indisponível</span>';
      return;
    }
    const fallback = core.fallback_active
      ? '<span class="ops-badge ops-badge--warn">fallback ativo</span>'
      : "";
    els.aiBody.innerHTML = `
      <div class="ops-row"><span>Provedor ativo</span><b>${esc(core.active_provider) || "—"}</b></div>
      <div class="ops-row"><span>Modelo</span><b>${esc(core.active_model) || "—"}</b></div>
      <div class="ops-row"><span>Router</span><b>${core.router_ready ? "pronto" : "sem provedor"} ${fallback}</b></div>
    `;
  }

  function renderProviders(providers) {
    if (!els.providersBody) return;
    if (!providers || !providers.length) {
      els.providersBody.innerHTML = '<span class="ops-muted">nenhum provedor</span>';
      return;
    }
    els.providersBody.innerHTML = providers
      .map(
        (p) => `
        <div class="ops-row">
          <span>
            <span class="ops-dot ${p.configured ? "ops-dot--ok" : "ops-dot--off"}" aria-hidden="true"></span>
            ${esc(p.name)}
            ${p.model ? `<span class="ops-muted">(${esc(p.model)})</span>` : ""}
          </span>
          <b>${esc(p.status)}</b>
        </div>`
      )
      .join("");
  }

  function renderSystem(sys) {
    if (!els.systemBody) return;
    els.systemBody.innerHTML = `
      <div class="ops-row"><span>Banco de dados</span><b>${sys?.db ? "ok" : "erro"}</b></div>
      <div class="ops-row"><span>Versão</span><b>${esc(sys?.version) || "—"}</b></div>
    `;
  }

  function renderContext(ctx, localFirst) {
    if (!els.contextBody) return;
    const use = ctx?.used_tokens_est != null ? ctx.used_tokens_est : "—";
    const limit = ctx?.limit_tokens != null ? ctx.limit_tokens : "—";
    const truncated = ctx?.truncated_est
      ? '<span class="ops-badge ops-badge--warn">truncado</span>'
      : '<span class="ops-badge ops-badge--ok">integral</span>';
    const policy = localFirst
      ? '<span class="ops-badge ops-badge--ok">LOCAL_FIRST</span>'
      : '<span class="ops-badge">LLM-first</span>';
    els.contextBody.innerHTML = `
      <div class="ops-row"><span>Contexto (estimado)</span><b>${esc(use)} / ${esc(limit)} tok ${truncated}</b></div>
      <div class="ops-row"><span>Política</span><b>${policy}</b></div>
      <div class="ops-muted ops-note">Operações conhecidas são executadas deterministicamente pela Intent Detection.</div>
    `;
  }

  function renderMemory(mem) {
    if (!els.memoryBody) return;
    if (!mem) return;
    const kinds = Object.entries(mem.by_kind || {})
      .map(
        ([k, v]) =>
          `<span class="ops-chip">${esc(k)}: <b>${v}</b></span>`
      )
      .join("");
    const recent = (mem.recent || [])
      .slice(0, 3)
      .map(
        (m) =>
          `<li class="ops-log">${esc(m.kind)} · ${esc((m.content || "").slice(0, 40))}</li>`
      )
      .join("");
    els.memoryBody.innerHTML = `
      <div class="ops-row"><span>Total</span><b>${mem.total}</b></div>
      <div class="ops-chips">${kinds || '<span class="ops-muted">—</span>'}</div>
      ${recent ? `<ul class="ops-list">${recent}</ul>` : ""}
    `;
  }

  function renderTools(tools) {
    if (!els.toolsBody) return;
    if (!tools) return;
    const byPerm = Object.entries(tools.by_permission || {})
      .sort((a, b) => Number(a[0]) - Number(b[0]))
      .map(([k, v]) => `<span class="ops-chip">nível ${k}: <b>${v}</b></span>`)
      .join("");
    els.toolsBody.innerHTML = `
      <div class="ops-row"><span>Total</span><b>${tools.total}</b></div>
      <div class="ops-chips">${byPerm || '<span class="ops-muted">—</span>'}</div>
    `;
  }

  function renderTasks(tasks) {
    if (!els.tasksBody) return;
    if (!tasks) return;
    const execs = (tasks.recent_executions || [])
      .slice(0, 4)
      .map(
        (a) =>
          `<li class="ops-log">${esc(a.tool || a.action)} — ${
            a.allowed === null ? "aguardando" : a.allowed ? "permitido" : "negado"
          }</li>`
      )
      .join("");
    els.tasksBody.innerHTML = `
      <div class="ops-row"><span>Pendentes de aprovação</span><b>${tasks.pending_approvals}</b></div>
      <div class="ops-row"><span>Aprovadas / Negadas</span><b>${tasks.approved} / ${tasks.denied}</b></div>
      ${execs ? `<ul class="ops-list">${execs}</ul>` : ""}
    `;
  }

  function renderAtlas(atlas) {
    if (!els.atlasBody) return;
    if (!atlas) return;
    els.atlasBody.innerHTML = `
      <div class="ops-row"><span>Estado</span><b>${atlas.enabled ? "habilitado" : "desabilitado"}</b></div>
      <div class="ops-row"><span>Configurado</span><b>${atlas.configured ? "sim" : "não"}</b></div>
      <div class="ops-row"><span>Base URL</span><b>${esc(atlas.base_url || "—")}</b></div>
      <div class="ops-muted ops-note">${esc(atlas.detail || "")}</div>
    `;
  }

  function renderEvents(events) {
    if (!els.eventsBody) return;
    if (!events || !events.length) {
      els.eventsBody.innerHTML = '<span class="ops-muted">sem eventos ainda</span>';
      return;
    }
    els.eventsBody.innerHTML =
      "<ul class=\"ops-list ops-events\">" +
      events
        .map((e) => {
          const prov = e.provider ? ` · ${esc(e.provider)}` : "";
          const lat = e.latency_ms != null ? ` · ${e.latency_ms}ms` : "";
          return `<li class="ops-log">
            <span class="ops-time">${time(e.created_at)}</span>
            <span class="ops-chip">${esc(e.event_type)}</span>
            <span class="ops-muted">${esc(e.status || "")}${prov}${lat}</span>
          </li>`;
        })
        .join("") +
      "</ul>";
  }

  async function apiJSON(path) {
    const res = await fetch(path);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  async function load() {
    try {
      const data = await apiJSON("/api/ops/overview");
      renderAI(data.ai_core);
      renderProviders(data.providers);
      renderSystem(data.system);
      renderContext(data.context, data.local_first);
      renderMemory(data.memory);
      renderTools(data.tools);
      renderTasks(data.tasks);
      renderAtlas(data.atlas);
      renderEvents(data.recent_events);
    } catch (err) {
      const bodies = Object.values(els).filter((b) => b && b.classList?.contains("ops-body"));
      bodies.forEach((b) => (b.innerHTML = '<span class="ops-muted ops-error">erro ao carregar Central de Operações</span>'));
      console.error("ops:", err);
    }
  }

  function init() {
    if (els.tabChat) els.tabChat.addEventListener("click", () => setView(false));
    if (els.tabOps) els.tabOps.addEventListener("click", () => setView(true));
    if (els.refresh) els.refresh.addEventListener("click", () => load());
  }

  return { init, load, setView, isOpen: () => open };
})();

if (typeof window !== "undefined") {
  window.addEventListener("DOMContentLoaded", OpsView.init);
  /* expõe para possível reuso/recarga do status */
  window.OpsView = OpsView;
}
