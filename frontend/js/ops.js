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
    ovMode: document.getElementById("ops-ov-mode"),
    ovBadge: document.getElementById("ops-ov-badge"),
    ovContext: document.getElementById("ops-ov-context"),
    ovPolicy: document.getElementById("ops-ov-policy"),
    ovActivity: document.getElementById("ops-ov-activity"),
    aiCard: document.getElementById("ops-ai"),
    providersCard: document.getElementById("ops-providers"),
    systemCard: document.getElementById("ops-system"),
    toolsCard: document.getElementById("ops-tools"),
    tasksCard: document.getElementById("ops-tasks"),
    aiBody: document.getElementById("ops-ai-body"),
    providersBody: document.getElementById("ops-providers-body"),
    systemBody: document.getElementById("ops-system-body"),
    contextBody: document.getElementById("ops-context-body"),
    memoryBody: document.getElementById("ops-memory-body"),
    toolsBody: document.getElementById("ops-tools-body"),
    tasksBody: document.getElementById("ops-tasks-body"),
    atlasBody: document.getElementById("ops-atlas-body"),
    proactiveBody: document.getElementById("ops-proactive-body"),
    computerBody: document.getElementById("ops-computer-body"),
    computerAgentBody: document.getElementById("ops-computer-agent-body"),
    identityBody: document.getElementById("ops-identity-body"),
    devicesCard: document.getElementById("ops-devices"),
    devicesBody: document.getElementById("ops-devices-body"),
    eventsBody: document.getElementById("ops-events-body"),
  };

  const CHAT_ROOT = document.querySelector(".shell");
  const COMPOSER = document.getElementById("input-form");

  let open = false;
  let orbState = "idle";

  /* Sincroniza com o Orb (app.js) — integração visual com ferramenta em execução §13.
     Não altera contrato/backend; apenas observa o estado decorativo. Fase 19.6:
     rótulos conduzidos pelo mapa de estados /js/vega-state.js (um rótulo, uma fonte). */
  function activityLabel(state) {
    const st = state || "idle";
    if (window.VegaState) return window.VegaState.stateMeta(st).label;
    const fb = {
      executing: "executando",
      thinking: "pensando",
      speaking: "falando",
      listening: "ouvindo",
      working: "operando",
      planning: "planejando",
      perceiving: "percebendo",
      observing: "observando",
      verifying: "verificando",
      recovering: "recuperando",
      waiting_confirmation: "aguardando aprovação",
      success: "concluído",
      warning: "atenção",
      error: "erro",
      idle: "ociosa",
      offline: "off-line",
    };
    return fb[st] || "ociosa";
  }

  function notifyOrb(state) {
    orbState = state || "idle";
    if (open && els.ovActivity) els.ovActivity.textContent = activityLabel(orbState);
  }

  /* ---------- navegação ---------- */
  function setView(showOps) {
    open = showOps;
    if (els.view) els.view.hidden = !showOps;
    if (CHAT_ROOT) CHAT_ROOT.hidden = showOps;
    if (COMPOSER) COMPOSER.hidden = showOps;
    if (els.tabChat) els.tabChat.classList.toggle("is-active", !showOps);
    if (els.tabOps) els.tabOps.classList.toggle("is-active", showOps);
    if (showOps && open) load();
    // Fase 12.5 — coordena com o painel Remote (só um sub-view ativo).
    if (window.RemoteView && window.RemoteView.isOpen && window.RemoteView.isOpen()) {
      window.RemoteView.setView(false);
    }
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
    const optionalNote = atlas.optional
      ? '<div class="ops-muted ops-note">Atlas é opcional — não é dependência da VEGA.</div>'
      : "";
    els.atlasBody.innerHTML = `
      <div class="ops-row"><span>Estado</span><b>${atlas.enabled ? "habilitado" : "desabilitado"}</b></div>
      <div class="ops-row"><span>Configurado</span><b>${atlas.configured ? "sim" : "não"}</b></div>
      <div class="ops-row"><span>Base URL</span><b>${esc(atlas.base_url || "—")}</b></div>
      <div class="ops-muted ops-note">${esc(atlas.detail || "")}</div>
      ${optionalNote}
    `;
  }

  function renderProactive(pro) {
    if (!els.proactiveBody) return;
    if (!pro) return;
    const by = pro.events_by_decision || {};
    const notified = by.notify ?? 0;
    els.proactiveBody.innerHTML = `
      <div class="ops-row"><span>Capacidade</span><b>${pro.enabled ? "habilitada" : "desabilitada"}</b></div>
      <div class="ops-row"><span>Scheduler</span><b>${pro.scheduler_enabled ? "ativo" : "inativo"}</b></div>
      <div class="ops-row"><span>Eventos recebidos</span><b>${pro.events_received}</b></div>
      <div class="ops-row"><span>Notificações (web/remoto)</span><b>${pro.messages_delivered_web} / ${pro.messages_delivered_remote}</b></div>
      <div class="ops-row"><span>Pendentes (adiadas)</span><b>${pro.deferred_pending}</b></div>
      <div class="ops-row"><span>Schedules (ativos)</span><b>${pro.schedules_total} (${pro.schedules_enabled})</b></div>
      <div class="ops-row"><span>Silêncio</span><b>${esc(pro.quiet_hours || "—")}</b></div>
      <div class="ops-muted ops-note">ignoradas ${by.ignore ?? 0} · adiadas ${by.defer ?? 0} · notificadas ${notified} · LLM ${pro.llm_invocations}</div>
    `;
  }

  function renderComputerActions(c) {
    if (!els.computerBody) return;
    if (!c) return;
    const caps = c.capabilities || {};
    const ev = c.events || {};
    const byType = Object.entries(c.actions_by_type || {})
      .map(([k, v]) => `${k}:${v}`)
      .join(" · ");
    els.computerBody.innerHTML = `
      <div class="ops-row"><span>Camada</span><b>${c.enabled ? "habilitada" : "desabilitada"}</b></div>
      <div class="ops-row"><span>Adapter</span><b>${esc((c.adapter || {}).name || "—")} (${(c.adapter || {}).available ? "disponível" : "indisponível"})</b></div>
      <div class="ops-row"><span>Ações executadas</span><b>${c.actions_total}</b></div>
      <div class="ops-row"><span>Rate limitadas</span><b>${c.rate_limited_count}</b></div>
      <div class="ops-row"><span>Por tipo</span><b>${esc(byType || "—")}</b></div>
      <div class="ops-row"><span>Eventos (ok/falha/negada)</span><b>${ev.executed ?? 0} / ${ev.failed ?? 0} / ${ev.rejected ?? 0}</b></div>
      <div class="ops-muted ops-note">caps: mouse ${caps.mouse ? "sim" : "não"} · teclado ${caps.keyboard ? "sim" : "não"} · janela ${caps.window_focus ? "sim" : "não"}</div>
    `;
  }

  function renderComputerAgent(c) {
    if (!els.computerAgentBody) return;
    if (!c) return;
    const lim = c.limits || {};
    const by = c.by_status || {};
    const chips = Object.entries(by).map(([k, v]) => `${k}:${v}`).join(" · ");
    els.computerAgentBody.innerHTML = `
      <div class="ops-row"><span>Agente</span><b>${c.enabled ? "habilitado" : `<span class="ops-muted">desabilitado</span>`}</b></div>
      <div class="ops-row"><span>Autonomia</span><b>${esc(c.autonomy_default || "C1")}</b></div>
      <div class="ops-row"><span>Tarefas ativas</span><b>${c.active_tasks ?? 0}</b></div>
      <div class="ops-row"><span>Concluídas / Falhas / Canceladas</span><b>${c.completed_tasks ?? 0} / ${c.failed_tasks ?? 0} / ${c.cancelled_tasks ?? 0}</b></div>
      <div class="ops-row"><span>Ações · Recuperações</span><b>${c.actions_total ?? 0} · ${c.recoveries ?? 0}</b></div>
      <div class="ops-row"><span>Loops impedidos</span><b>${c.loops_prevented ?? 0}</b></div>
      <div class="ops-row"><span>Confirmações</span><b>${c.confirmations_required ?? 0}</b></div>
      <div class="ops-row"><span>Média ações / passos</span><b>${c.average_actions ?? 0} / ${c.average_steps ?? 0}</b></div>
      <div class="ops-muted ops-note">tetos: ${lim.max_steps || "—"} passos · ${lim.max_actions || "—"} ações · ${lim.max_retries || "—"} retries · ${lim.timeout_seconds || "—"}s</div>
      <div class="ops-muted ops-note">estado: ${esc(chips || "—")}</div>
    `;
  }

  /* Fase 19.6 — timeline operacional do Computer Agent. Só eventos REAIS
     (recent_events prefixados "computer."); nenhum chain-of-thought fabricado. */
  const CAL_STEP = {
    "computer.task.created": { label: "tarefa criada", tone: "info" },
    "computer.task.started": { label: "tarefa iniciada", tone: "info" },
    "computer.task.planned": { label: "plano traçado", tone: "info" },
    "computer.task.completed": { label: "tarefa concluída", tone: "ok" },
    "computer.task.failed": { label: "tarefa falhou", tone: "fail" },
    "computer.task.cancelled": { label: "tarefa cancelada", tone: "warn" },
    "computer.task.waiting_confirmation": { label: "aguardando aprovação", tone: "wait" },
    "computer.action.requested": { label: "ação solicitada", tone: "info" },
    "computer.action.executed": { label: "ação executada", tone: "ok" },
    "computer.action.failed": { label: "ação falhou", tone: "fail" },
    "computer.observation.received": { label: "ambiente lido", tone: "ok" },
    "computer.verification.started": { label: "verificando resultado", tone: "info" },
    "computer.verification.success": { label: "verificação ok", tone: "ok" },
    "computer.verification.failed": { label: "verificação falhou", tone: "fail" },
    "computer.recovery.started": { label: "recuperando", tone: "info" },
    "computer.recovery.completed": { label: "recuperação ok", tone: "ok" },
    "computer.loop.prevented": { label: "loop impedido", tone: "warn" },
    "computer.security.prompt_injection": { label: "prompt injection", tone: "warn" },
    "computer.security.autonomy_blocked": { label: "autonomia bloqueada", tone: "warn" },
  };

  function calStepOf(eventType) {
    const known = CAL_STEP[eventType];
    if (known) return known;
    let tone = "info";
    if (/(failed|cancelled|blocked|prevented|injection)$/.test(eventType)) tone = "warn";
    else if (/waiting_confirmation/.test(eventType)) tone = "wait";
    else if (/(completed|success|executed|received)/.test(eventType)) tone = "ok";
    return { label: eventType.replace(/^computer\./, "").replace(/_/g, " "), tone };
  }

  function renderComputerTimeline(c, events) {
    const tl = document.getElementById("ops-computer-timeline");
    if (!tl) return;
    tl.innerHTML = "";
    const pool = (events || []).filter((e) => (e.event_type || "").startsWith("computer."));
    const ativo = !!c && c.enabled && (c.active_tasks ?? 0) > 0;
    if (!pool.length) {
      const li = document.createElement("li");
      li.className = "cal-item cal-item--empty";
      li.textContent = ativo ? "Computer Agent em atividade..." : "Sem atividade operacional registrada.";
      tl.appendChild(li);
      return;
    }
    const items = pool.slice(-7);
    items.forEach((e) => {
      const step = calStepOf(e.event_type);
      const li = document.createElement("li");
      li.className = `cal-item cal-item--${step.tone}`;
      const dot = document.createElement("span");
      dot.className = "cal-dot";
      dot.setAttribute("aria-hidden", "true");
      const txt = document.createElement("span");
      txt.className = "cal-text";
      txt.textContent = step.label;
      const ts = document.createElement("time");
      ts.className = "cal-time";
      ts.dateTime = e.created_at || "";
      ts.textContent = time(e.created_at);
      li.append(dot, txt, ts);
      tl.appendChild(li);
    });
  }

  /* Estado "ao vivo" a partir de tarefas reais do Computer Agent (by_status). */
  function computerActiveState(c) {
    if (!c || !c.enabled || !(c.active_tasks > 0) || !c.by_status) return null;
    const order = ["planning", "perceiving", "executing", "observing", "verifying", "recovering", "waiting_confirmation"];
    for (const k of order) if (c.by_status[k] > 0) return k;
    return null;
  }

  function renderIdentity(id) {
    if (!els.identityBody) return;
    if (!id) return;
    els.identityBody.innerHTML = `
      <div class="ops-row"><span>Nome de exibição</span><b>${esc(id.name || "VEGA")}</b></div>
      <div class="ops-row"><span>Tom</span><b>${esc(id.tone || "—")}</b></div>
      <div class="ops-row"><span>Verborragia</span><b>${esc(id.verbosity || "—")}</b></div>
      <div class="ops-row"><span>Proatividade</span><b>${esc(id.proactivity || "—")}</b></div>
      <div class="ops-row"><span>Humor / Formalidade</span><b>${esc(id.humor || "—")} / ${esc(id.formality || "—")}</b></div>
      <div class="ops-muted ops-note">código/repositório permanecem JARVIS</div>
    `;
  }

  function renderDevices(devices) {
    if (!els.devicesBody) return;
    if (!devices) return;
    const byStatus = Object.entries(devices.by_status || {})
      .map(([k, v]) => `<span class="ops-chip">${esc(k)}: <b>${v}</b></span>`)
      .join("");
    const byPlat = Object.entries(devices.by_platform || {})
      .map(([k, v]) => `<span class="ops-chip">${esc(k)}: <b>${v}</b></span>`)
      .join("");
    const enabled = devices.enabled ? "habilitado" : "desabilitado";
    els.devicesBody.innerHTML = `
      <div class="ops-row"><span>Bridge</span><b>${enabled}</b></div>
      <div class="ops-row"><span>Dispositivos (total / confiáveis)</span><b>${devices.total ?? 0} / ${devices.trusted ?? 0} (teto ${devices.max_devices ?? "—"})</b></div>
      <div class="ops-row"><span>Heartbeat</span><b>${devices.heartbeat_seconds ?? "—"}s · reconexão ${devices.reconnect_enabled ? "ativa" : "off"}</b></div>
      <div class="ops-row"><span>Por estado</span></div>
      <div class="ops-chips">${byStatus || '<span class="ops-muted">—</span>'}</div>
      <div class="ops-row"><span>Por plataforma</span></div>
      <div class="ops-chips">${byPlat || '<span class="ops-muted">—</span>'}</div>
      ${devices.detail ? `<div class="ops-muted ops-note">${esc(devices.detail)}</div>` : ""}
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

  /* ---------- estado geral (Nível 1) §13 ---------- */
  function setCardState(card, state) {
    if (!card) return;
    card.classList.remove("has-warn", "has-err");
    if (state === "warn") card.classList.add("has-warn");
    else if (state === "err") card.classList.add("has-err");
  }

  function renderOverview(data) {
    if (!els.ovBadge) return;
    const core = data.ai_core || {};
    const sys = data.system || {};
    const routerOk = !!core.router_ready;
    const dbOk = sys?.db !== false;
    const localFirst = !!data.local_first;

    // Nível de estado global: READY / ATENÇÃO / ERRO
    let state = "ok";
    let label = "PRONTO";
    if (dbOk === false) {
      state = "err";
      label = "BANCO DE DADOS";
    } else if (core.fallback_active) {
      state = "warn";
      label = "FALLBACK";
    } else if (!routerOk) {
      state = "warn";
      label = "SEM PROVEDOR IA";
    }

    els.ovBadge.textContent = localFirst ? "READY" : "PRONTO";
    els.ovBadge.className =
      "ops-ov-badge " +
      (state === "ok"
        ? "is-ok"
        : state === "warn"
          ? "is-warn"
          : "is-err");
    if (els.ovMode) els.ovMode.textContent = localFirst ? "LOCAL" : "LLM-FIRST";
    if (els.ovContext) {
      const ctx = data.context || {};
      const use = ctx.used_tokens_est != null ? ctx.used_tokens_est : "—";
      const limit = ctx.limit_tokens != null ? ctx.limit_tokens : "—";
      els.ovContext.textContent = `${use} / ${limit}`;
    }
    if (els.ovPolicy) els.ovPolicy.textContent = localFirst ? "LOCAL_FIRST" : "LLM-FIRST";

    // Estados por card (sistema/ferramentas)
    const ex = data.tasks?.recent_executions || [];
    const hasPending = ex.some((a) => a.action === "tool.execute" && a.allowed === null);
    setCardState(els.systemCard, dbOk === false ? "err" : null);
    setCardState(els.providersCard, !routerOk ? "warn" : null);
    setCardState(els.aiCard, core.fallback_active ? "warn" : null);
    setCardState(els.tasksCard, hasPending ? "warn" : null);

    if (els.ovActivity) {
      const ax = data.tasks?.recent_executions || [];
      const pending = ax.some((a) => a.action === "tool.execute" && a.allowed === null);
      const label = activityLabel(orbState);
      els.ovActivity.textContent =
        label === "ociosa" ? (pending ? "aprovação pendente" : "ociosa") : label;
    }
  }

  async function apiJSON(path) {
    const res = await fetch(path);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  async function load() {
    try {
      const data = await apiJSON("/api/ops/overview");
      renderOverview(data);
      renderAI(data.ai_core);
      renderProviders(data.providers);
      renderSystem(data.system);
      renderContext(data.context, data.local_first);
      renderMemory(data.memory);
      renderTools(data.tools);
      renderTasks(data.tasks);
      renderAtlas(data.atlas);
      renderProactive(data.proactive);
      renderComputerActions(data.computer_actions);
      renderComputerAgent(data.computer_agent);
      renderComputerTimeline(data.computer_agent, data.recent_events);
      renderIdentity(data.identity);
      renderDevices(data.devices);
      renderEvents(data.recent_events);
      // Fase 19.6 — atividade real do Computer Agent reflete no orb quando a Central
      // está aberta (via VegaUI; nunca inventa estado).
      if (window.VegaUI) {
        const active = computerActiveState(data.computer_agent);
        if (active) {
          const st = window.VegaState
            ? window.VegaState.resolveComputerTask(active)
            : "working";
          window.VegaUI.push(st);
        }
      }
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
    // Fase 17: stream proativo — nova mensagem proativa atualiza o card.
    if (window.EventSource) {
      const es = new EventSource("/api/proactive/stream");
      es.onmessage = (e) => {
        try {
          const entry = JSON.parse(e.data);
          if (entry && entry.type === "proactive.message") load();
        } catch (_) {
          /* ignora heartbeat/linhas não-JSON */
        }
      };
      es.onerror = () => {
        /* EventSource reconecta sozinho; sem ação decorativa aqui */
      };
    }
  }

  return { init, load, setView, notifyOrb, isOpen: () => open };
})();

if (typeof window !== "undefined") {
  window.addEventListener("DOMContentLoaded", OpsView.init);
  /* expõe para possível reuso/recarga do status */
  window.OpsView = OpsView;
}
