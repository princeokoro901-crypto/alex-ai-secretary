(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const keyInput = byId("api-key");
  const form = byId("connection-form");
  const clearButton = byId("clear-key");
  const connectButton = form.querySelector('button[type="submit"]');
  const STORAGE_KEY = "alex_api_key";

  try {
    const existing = sessionStorage.getItem(STORAGE_KEY);
    if (existing) keyInput.value = existing;
  } catch (_) {
    byId("key-help").textContent = "This browser blocks sessionStorage; the key will be used only while this page remains open.";
  }

  function setText(id, value) { byId(id).textContent = value == null ? "—" : String(value); }
  function setPill(element, text, state) {
    element.textContent = text;
    element.classList.remove("status-unknown", "status-good", "status-warn", "status-bad");
    element.classList.add(`status-${state}`);
  }
  function empty(target, message) {
    const p = document.createElement("p");
    p.className = "empty-state";
    p.textContent = message;
    target.replaceChildren(p);
  }
  function apiHeaders() {
    const headers = new Headers({ "Accept": "application/json" });
    const key = keyInput.value.trim();
    if (key) headers.set("Authorization", `Bearer ${key}`);
    return headers;
  }
  async function apiGet(path) {
    const response = await fetch(path, { method: "GET", headers: apiHeaders(), credentials: "same-origin", cache: "no-store", redirect: "error" });
    let body = null;
    try { body = await response.json(); } catch (_) { /* handled as an HTTP/API error below */ }
    if (!response.ok) {
      const reason = body && typeof body.detail === "string" ? body.detail : `Request failed (${response.status})`;
      throw new Error(`${response.status}: ${reason}`);
    }
    if (!body || typeof body !== "object") throw new Error("The API returned an unexpected response.");
    return body;
  }
  function showConnectorInventory(data) {
    const target = byId("connectors-list");
    target.replaceChildren();
    const items = Array.isArray(data.connectors) ? data.connectors : [];
    setText("connector-count", items.length);
    if (!items.length) return empty(target, "No connectors are registered in this runtime.");
    for (const item of items) {
      const card = document.createElement("article");
      card.className = "list-item";
      const title = document.createElement("h3");
      title.className = "list-title";
      title.textContent = item.name || item.connector_id || "Unnamed connector";
      const description = document.createElement("p");
      description.className = "list-description";
      description.textContent = item.description || "No description provided.";
      const meta = document.createElement("div");
      meta.className = "list-meta";
      for (const label of [item.connector_id, item.execution_policy, ...(Array.isArray(item.allowed_roles) ? item.allowed_roles.map((role) => `role: ${role}`) : [])]) {
        if (!label) continue;
        const tag = document.createElement("span");
        tag.className = "tag";
        tag.textContent = label;
        meta.append(tag);
      }
      card.append(title, description, meta);
      target.append(card);
    }
  }
  function showApprovals(data) {
    const target = byId("approvals-list");
    target.replaceChildren();
    const items = Array.isArray(data.approvals) ? data.approvals : [];
    setText("approval-count", items.length);
    setText("approval-value", items.length);
    setText("approval-detail", "Pending items returned by the API");
    if (!items.length) return empty(target, "There are no pending approvals.");
    for (const item of items) {
      const card = document.createElement("article");
      card.className = "list-item";
      const title = document.createElement("h3");
      title.className = "list-title";
      title.textContent = `Approval ${item.approval_id || "(unknown ID)"}`;
      const description = document.createElement("p");
      description.className = "list-description";
      description.textContent = item.action || "No action description provided.";
      const meta = document.createElement("div");
      meta.className = "list-meta";
      for (const label of [`Mission: ${item.mission_id || "unknown"}`, `Requester: ${item.requested_by || "unknown"}`, `Status: ${item.status || "unknown"}`]) {
        const tag = document.createElement("span");
        tag.className = "tag";
        tag.textContent = label;
        meta.append(tag);
      }
      card.append(title, description, meta);
      target.append(card);
    }
  }
  function showReadiness(data) {
    const target = byId("readiness-summary");
    target.replaceChildren();
    const ready = data.ready_for_live_gmail === true;
    setPill(byId("readiness-badge"), ready ? "Candidate only" : "Blocked", ready ? "warn" : "bad");
    const summary = document.createElement("div");
    summary.className = "readiness-line";
    const strong = document.createElement("strong");
    strong.textContent = `${data.blocker_count ?? 0} blocker(s) · ${data.warning_count ?? 0} warning(s)`;
    const detail = document.createElement("span");
    detail.textContent = data.note || "Readiness is advisory and does not enable integrations.";
    summary.append(strong, detail);
    target.append(summary);
    if (Array.isArray(data.blockers) && data.blockers.length) {
      const blockers = document.createElement("div");
      blockers.className = "readiness-line";
      const label = document.createElement("strong");
      label.textContent = "Current blockers";
      const text = document.createElement("span");
      text.textContent = data.blockers.join(", ");
      blockers.append(label, text);
      target.append(blockers);
    }
    if (Array.isArray(data.warnings) && data.warnings.length) {
      const warnings = document.createElement("div");
      warnings.className = "readiness-line";
      const label = document.createElement("strong");
      label.textContent = "Warnings";
      const text = document.createElement("span");
      text.textContent = data.warnings.join(", ");
      warnings.append(label, text);
      target.append(warnings);
    }
  }
  function showProtectedError(targetId, message) {
    empty(byId(targetId), message);
  }

  function showAIProviderStatus(data) {
    const status = byId("ai-provider-status");
    const aiCheckbox = byId("report-use-ai");
    if (data.configured === true && data.state === "configured_not_tested") {
      status.textContent = `AI provider configured (${data.model_label || "model label unavailable"}), but live connectivity has not been tested.`;
      aiCheckbox.disabled = false;
      aiCheckbox.title = "Selecting this sends the report title, audience, and notes to the configured provider.";
      byId("ai-provider-test").disabled = false;
    } else {
      status.textContent = data.message || "AI provider is not ready. The local report template remains available.";
      aiCheckbox.checked = false;
      aiCheckbox.disabled = true;
      aiCheckbox.title = "Configure a valid provider on the server before using AI mode.";
      byId("ai-provider-test").disabled = true;
    }
  }

  async function refreshDashboard() {
    connectButton.disabled = true;
    byId("connection-message").textContent = "Checking Alex…";
    setPill(byId("overall-status"), "Checking…", "unknown");
    try {
      const health = await apiGet("/health");
      const healthy = health.status === "ok";
      setPill(byId("overall-status"), healthy ? "Service online" : "Service issue", healthy ? "good" : "bad");
      setText("service-value", healthy ? "Online" : "Issue");
      setText("service-detail", `Alex Core v${health.version || "unknown"}`);
      setText("storage-value", health.orchestration_persistence ? "Persistent" : "In-memory / default");
      setText("storage-detail", `Backend: ${health.orchestration_backend || "unknown"}`);
      setText("audit-value", health.audit_integrity === true ? "Verified" : "Check needed");
      setText("audit-detail", `${health.audit_events ?? 0} audit event(s)`);
      setText("approval-value", health.pending_approvals ?? "—");
      setText("approval-detail", "Pending count from health endpoint");
      byId("connection-message").textContent = "Health check succeeded. Loading protected inventories…";

      const results = await Promise.allSettled([
        apiGet("/api/v1/connectors"),
        apiGet("/api/v1/approvals?status=pending"),
        apiGet("/api/v1/system/readiness"),
        apiGet("/api/v1/system/ai-provider-status")
      ]);
      if (results[0].status === "fulfilled") showConnectorInventory(results[0].value);
      else showProtectedError("connectors-list", `Connector inventory unavailable: ${results[0].reason.message}`);
      if (results[1].status === "fulfilled") showApprovals(results[1].value);
      else showProtectedError("approvals-list", `Approval list unavailable: ${results[1].reason.message}`);
      if (results[2].status === "fulfilled") showReadiness(results[2].value);
      else {
        setPill(byId("readiness-badge"), "Unavailable", "unknown");
        showProtectedError("readiness-summary", `Readiness requires administrator access: ${results[2].reason.message}`);
      }
      if (results[3].status === "fulfilled") showAIProviderStatus(results[3].value);
      else {
        byId("ai-provider-status").textContent = `AI provider status unavailable: ${results[3].reason.message}`;
        byId("report-use-ai").checked = false;
        byId("report-use-ai").disabled = true;
        byId("ai-provider-test").disabled = true;
      }
      const failed = results.filter((item) => item.status === "rejected").length;
      byId("connection-message").textContent = failed === 0
        ? "Connected. Inventory and administrator diagnostics loaded."
        : `Health check succeeded; ${failed} protected view(s) could not be loaded. Check the API key role and permissions.`;
    } catch (error) {
      setPill(byId("overall-status"), "Service unavailable", "bad");
      setText("service-value", "Unavailable");
      byId("connection-message").textContent = `Could not reach Alex: ${error.message}`;
    } finally {
      connectButton.disabled = false;
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      if (keyInput.value.trim()) sessionStorage.setItem(STORAGE_KEY, keyInput.value.trim());
      else sessionStorage.removeItem(STORAGE_KEY);
    } catch (_) { /* the current input remains usable without browser storage */ }
    await refreshDashboard();
  });
  clearButton.addEventListener("click", () => {
    keyInput.value = "";
    try { sessionStorage.removeItem(STORAGE_KEY); } catch (_) { /* storage may be blocked */ }
    byId("connection-message").textContent = "API key cleared from this page and session storage.";
    refreshDashboard();
  });


  byId("ai-provider-test").addEventListener("click", async () => {
    const button = byId("ai-provider-test");
    const result = byId("ai-provider-test-result");
    button.disabled = true;
    result.textContent = "Testing provider connection with a short probe…";
    try {
      const data = await apiWrite("/api/v1/system/ai-provider-test", {});
      result.textContent = data.connection_tested === true && data.state === "connected"
        ? `Live connection test succeeded for ${data.model_label || "configured model"}. No report notes were sent.`
        : "Provider response did not confirm a successful connection.";
    } catch (error) {
      result.textContent = `Connection test failed: ${error.message}. No report notes were sent.`;
    } finally {
      button.disabled = !(byId("report-use-ai").disabled === false);
    }
  });

  const reportTaskForm = byId("report-task-form");
  const reportTaskMessage = byId("report-task-message");
  const reportTaskResult = byId("report-task-result");
  const reportDownloadButton = byId("report-download");
  let latestReportMarkdown = "";
  let latestReportTitle = "alex-report";
  reportTaskForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const title = byId("report-title").value.trim();
    const audience = byId("report-audience").value.trim();
    const notes = byId("report-notes").value;
    if (!title || !notes.trim()) {
      reportTaskMessage.textContent = "Enter a report title and source notes.";
      return;
    }
    const button = byId("report-task-submit");
    button.disabled = true;
    reportDownloadButton.disabled = true;
    reportTaskResult.hidden = true;
    const useAI = byId("report-use-ai").checked;
    reportTaskMessage.textContent = useAI ? "Sending notes to the configured AI provider and verifying the response…" : "Preparing report locally and verifying the output…";
    try {
      const endpoint = useAI ? "/api/v1/workspace/ai-draft-report" : "/api/v1/workspace/draft-report";
      const data = await apiWrite(endpoint, { title, audience, notes });
      if (data.status !== "completed" || data.verification?.passed !== true || typeof data.output_markdown !== "string") {
        throw new Error("Alex returned a result that did not pass verification.");
      }
      latestReportMarkdown = data.output_markdown;
      latestReportTitle = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "alex-report";
      reportTaskResult.textContent = latestReportMarkdown;
      reportTaskResult.hidden = false;
      reportDownloadButton.disabled = false;
      const passed = Object.values(data.verification.checks || {}).filter(Boolean).length;
      const modeLabel = data.ai_model_used ? `AI model (${data.model_label || "configured provider"})` : "local template";
      reportTaskMessage.textContent = `Draft prepared · ${data.source_line_count} source line(s) · ${passed} verification checks passed · ${modeLabel} · task ${data.task_id}. Review before use.`;
    } catch (error) {
      reportTaskMessage.textContent = `Report preparation failed: ${error.message}`;
    } finally { button.disabled = false; }
  });
  reportDownloadButton.addEventListener("click", () => {
    if (!latestReportMarkdown) return;
    const blob = new Blob([latestReportMarkdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${latestReportTitle}.md`;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  });

  const missionList = byId("mission-list");
  async function refreshMissionList() {
    const button = byId("mission-list-refresh");
    button.disabled = true;
    missionList.replaceChildren();
    empty(missionList, "Loading missions…");
    try {
      const data = await apiGet("/api/v1/missions");
      const items = Array.isArray(data.missions) ? data.missions : [];
      setText("mission-list-count", `${items.length} mission(s)`);
      missionList.replaceChildren();
      if (!items.length) return empty(missionList, "No missions have been created in this runtime.");
      for (const item of items) {
        const contract = item.contract || {};
        const card = document.createElement("article");
        card.className = "list-item";
        const title = document.createElement("h3");
        title.className = "list-title";
        title.textContent = contract.objective || contract.mission_id || "Untitled mission";
        const detail = document.createElement("p");
        detail.className = "list-description";
        detail.textContent = `ID: ${contract.mission_id || "unknown"} · Status: ${item.status || "unknown"} · Budget: ${contract.budget ?? 0}`;
        const inspect = document.createElement("button");
        inspect.type = "button";
        inspect.className = "button secondary";
        inspect.textContent = "Inspect";
        inspect.addEventListener("click", () => {
          byId("mission-lookup-id").value = contract.mission_id || "";
          byId("mission-lookup-form").requestSubmit();
        });
        card.append(title, detail, inspect);
        missionList.append(card);
      }
    } catch (error) {
      setText("mission-list-count", "Unavailable");
      empty(missionList, `Mission list unavailable: ${error.message}`);
    } finally { button.disabled = false; }
  }
  byId("mission-list-refresh").addEventListener("click", refreshMissionList);
  async function refreshMissionRecoveryQueue() {
    const target = byId("mission-recovery-list");
    target.replaceChildren();
    empty(target, "Loading recovery queue…");
    try {
      const data = await apiGet("/api/v1/missions/recovery-queue");
      const items = Array.isArray(data.missions) ? data.missions : [];
      setText("mission-recovery-count", `${items.length} awaiting review`);
      target.replaceChildren();
      if (!items.length) return empty(target, "No missions are awaiting restart recovery review.");
      for (const mission of items) {
        const card = document.createElement("article"); card.className = "list-item";
        const title = document.createElement("h3"); title.className = "list-title"; title.textContent = mission.contract?.objective || mission.contract?.mission_id || "Recovered mission";
        const detail = document.createElement("p"); detail.className = "list-description"; detail.textContent = `ID: ${mission.contract?.mission_id || "unknown"} · Status: ${mission.status} · Checkpoints: ${(mission.checkpoints || []).length}`;
        const reason = document.createElement("textarea"); reason.maxLength = 1000; reason.required = true; reason.placeholder = "Reason for recovery decision"; reason.setAttribute("aria-label", "Reason for recovery decision");
        const actions = document.createElement("div"); actions.className = "input-row";
        for (const [decision, label] of [["keep_paused", "Keep paused"], ["stop", "Stop mission"]]) {
          const button = document.createElement("button"); button.type = "button"; button.className = "button secondary"; button.textContent = label;
          button.addEventListener("click", async () => {
            const text = reason.value.trim();
            if (!text) { byId("mission-recovery-message").textContent = "Enter a reason before recording a decision."; reason.focus(); return; }
            if (!globalThis.confirm(`${label}? This will not resume or retry work.`)) return;
            button.disabled = true;
            try {
              await apiWrite(`/api/v1/missions/${encodeURIComponent(mission.contract.mission_id)}/recovery-decision`, {decision, reason: text});
              byId("mission-recovery-message").textContent = `Recorded: ${label} for ${mission.contract.mission_id}.`;
              await refreshMissionRecoveryQueue(); await refreshMissionList();
            } catch (error) {
              byId("mission-recovery-message").textContent = `Decision request returned: ${error.message}. Refresh the mission and pending-audit views before taking further action.`;
              button.disabled = false;
              // A 503 can mean the decision was saved but its audit write failed.
              // Refresh both views so the operator sees the persisted decision and can retry audit delivery.
              await refreshMissionRecoveryQueue();
              await refreshPendingRecoveryAudits();
              await refreshMissionList();
            }
          }); actions.append(button);
        }
        card.append(title, detail, reason, actions); target.append(card);
      }
    } catch (error) { setText("mission-recovery-count", "Unavailable"); empty(target, `Recovery queue unavailable: ${error.message}`); }
  }
  byId("mission-recovery-refresh").addEventListener("click", refreshMissionRecoveryQueue);

  async function refreshPendingRecoveryAudits() {
    const target = byId("mission-audit-pending-list");
    target.replaceChildren();
    empty(target, "Loading pending audit deliveries…");
    try {
      const data = await apiGet("/api/v1/missions/recovery-audit/pending");
      const items = Array.isArray(data.pending) ? data.pending : [];
      setText("mission-audit-pending-count", `${items.length} pending`);
      target.replaceChildren();
      if (!items.length) return empty(target, "No recovery audit deliveries are pending.");
      for (const item of items) {
        const card = document.createElement("article"); card.className = "list-item";
        const title = document.createElement("h3"); title.className = "list-title"; title.textContent = item.objective || item.mission_id || "Recovery decision";
        const detail = document.createElement("p"); detail.className = "list-description";
        detail.textContent = `Mission: ${item.mission_id || "unknown"} · Decision: ${item.decision || "unknown"} · Actor: ${item.actor_id || "unknown"} · Status: ${item.mission_status || "unknown"} · Audit: pending`;
        const retry = document.createElement("button"); retry.type = "button"; retry.className = "button secondary"; retry.textContent = "Retry audit delivery";
        retry.addEventListener("click", async () => {
          retry.disabled = true;
          byId("mission-audit-pending-message").textContent = `Retrying audit delivery for ${item.mission_id}… The mission decision will not be repeated.`;
          try {
            const result = await apiWrite(`/api/v1/missions/${encodeURIComponent(item.mission_id)}/recovery-audit/retry`, {});
            byId("mission-audit-pending-message").textContent = result.already_recorded
              ? `Audit was already recorded for ${item.mission_id}; no duplicate event was created.`
              : `Audit record delivered for ${item.mission_id}. Mission state was not changed.`;
            await refreshPendingRecoveryAudits();
          } catch (error) {
            byId("mission-audit-pending-message").textContent = `Audit retry failed for ${item.mission_id}: ${error.message}. The decision remains saved; refresh and retry when storage is available.`;
            retry.disabled = false;
          }
        });
        card.append(title, detail, retry); target.append(card);
      }
    } catch (error) {
      setText("mission-audit-pending-count", "Unavailable");
      empty(target, `Pending audit queue unavailable: ${error.message}`);
    }
  }
  byId("mission-audit-pending-refresh").addEventListener("click", refreshPendingRecoveryAudits);

  const missionCreateForm = byId("mission-create-form");
  const missionLookupForm = byId("mission-lookup-form");
  const missionMessage = byId("mission-message");
  const missionResult = byId("mission-result");
  let currentMissionId = "";
  function showMission(data) {
    missionResult.textContent = JSON.stringify(data, null, 2);
    missionResult.hidden = false;
    const contract = data.contract || {};
    currentMissionId = contract.mission_id || "";
    if (currentMissionId) byId("mission-lookup-id").value = currentMissionId;
    const assigned = Array.isArray(data.assigned_agents) ? data.assigned_agents : [];
    setText("assigned-agents", assigned.length ? `Assigned agents: ${assigned.join(", ")}` : "No agents assigned yet.");
    byId("assign-agent-button").disabled = !currentMissionId;
  }
  async function refreshAgentInventory() {
    const select = byId("agent-select");
    const button = byId("agent-list-refresh");
    button.disabled = true;
    select.replaceChildren(new Option("Loading agents…", ""));
    try {
      const data = await apiGet("/api/v1/workforce/agents");
      const agents = Array.isArray(data.agents) ? data.agents : [];
      select.replaceChildren(new Option(agents.length ? "Choose an agent" : "No registered agents", ""));
      for (const agent of agents) {
        const id = agent.agent_id || agent.id;
        if (!id) continue;
        const capabilities = Array.isArray(agent.capabilities) ? agent.capabilities.join(", ") : "";
        select.add(new Option(`${agent.name || id}${capabilities ? ` — ${capabilities}` : ""}`, id));
      }
      if (!agents.length) setText("assigned-agents", "No registered agents were returned by the API.");
    } catch (error) {
      select.replaceChildren(new Option("Agent inventory unavailable", ""));
      missionMessage.textContent = `Could not load agents: ${error.message}`;
    } finally { button.disabled = false; }
  }
  byId("agent-list-refresh").addEventListener("click", refreshAgentInventory);
  byId("assign-agent-button").addEventListener("click", async () => {
    const agentId = byId("agent-select").value;
    if (!currentMissionId) { missionMessage.textContent = "Inspect a mission before assigning an agent."; return; }
    if (!agentId) { missionMessage.textContent = "Choose a registered agent first."; return; }
    byId("assign-agent-button").disabled = true;
    missionMessage.textContent = "Assigning agent…";
    try {
      const data = await apiWrite(`/api/v1/missions/${encodeURIComponent(currentMissionId)}/agents`, { agent_id: agentId });
      showMission(data);
      missionMessage.textContent = `Agent ${agentId} assigned. Mission has not been started or executed.`;
      await refreshMissionList();
    } catch (error) {
      missionMessage.textContent = `Agent assignment failed: ${error.message}`;
    } finally { byId("assign-agent-button").disabled = !currentMissionId; }
  });
  async function apiWrite(path, payload) {
    const headers = apiHeaders();
    headers.set("Content-Type", "application/json");
    const response = await fetch(path, { method: "POST", headers, credentials: "same-origin", cache: "no-store", redirect: "error", body: JSON.stringify(payload) });
    let body = null;
    try { body = await response.json(); } catch (_) { /* converted to a safe error below */ }
    if (!response.ok) {
      const reason = body && typeof body.detail === "string" ? body.detail : `Request failed (${response.status})`;
      throw new Error(`${response.status}: ${reason}`);
    }
    if (!body || typeof body !== "object") throw new Error("The API returned an unexpected response.");
    return body;
  }
  missionCreateForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const objective = byId("mission-objective").value.trim();
    const budget = Number(byId("mission-budget").value);
    const scopeText = byId("mission-scope").value.trim();
    if (!objective || !Number.isFinite(budget) || budget < 0) {
      missionMessage.textContent = "Enter an objective and a valid non-negative budget.";
      return;
    }
    const missionId = `mission-${(globalThis.crypto && crypto.randomUUID) ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
    missionMessage.textContent = "Creating mission…";
    try {
      const data = await apiWrite("/api/v1/missions", { mission_id: missionId, objective, budget, scope: scopeText ? [scopeText] : [], verification_required: true, success_conditions: [], allowed_tools: [], allowed_data: [], forbidden_actions: [] });
      showMission(data);
      missionMessage.textContent = "Mission created. It has not been assigned, started, or executed.";
      await refreshMissionList();
    } catch (error) {
      missionMessage.textContent = `Mission creation failed: ${error.message}`;
    }
  });
  missionLookupForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const missionId = byId("mission-lookup-id").value.trim();
    if (!missionId || missionId.includes("/") || missionId.includes("\\")) {
      missionMessage.textContent = "Enter a valid mission ID.";
      return;
    }
    missionMessage.textContent = "Loading mission…";
    try {
      const data = await apiGet(`/api/v1/missions/${encodeURIComponent(missionId)}`);
      showMission(data);
      missionMessage.textContent = "Mission details loaded. No action was executed.";
    } catch (error) {
      missionMessage.textContent = `Mission lookup failed: ${error.message}`;
    }
  });
  const historyTarget = byId("orchestration-history-list");
  async function refreshOrchestrationHistory() {
    const message = byId("orchestration-history-message");
    message.textContent = "Loading recent orchestration runs…";
    try {
      const data = await apiGet("/api/v1/orchestrations?limit=25");
      const runs = Array.isArray(data.orchestrations) ? data.orchestrations : [];
      historyTarget.replaceChildren();
      setText("orchestration-history-count", `${runs.length} / ${data.total ?? runs.length}`);
      if (!runs.length) empty(historyTarget, "No orchestration runs have been recorded yet.");
      for (const run of runs) {
        const card = document.createElement("article"); card.className = "list-item";
        const title = document.createElement("h4"); title.className = "list-title";
        title.textContent = `${run.status || "unknown"} · ${run.orchestration_id || "unknown ID"}`;
        const description = document.createElement("p"); description.className = "list-description";
        description.textContent = `Mission: ${run.mission_id || "unknown"} · Agent: ${run.selected_agent_id || "unknown"} · Recovery: ${run.recovery_state || "none"}`;
        const inspect = document.createElement("button"); inspect.type = "button"; inspect.className = "button secondary"; inspect.textContent = "Inspect run";
        inspect.addEventListener("click", async () => {
          message.textContent = `Loading run ${run.orchestration_id}…`;
          try {
            const detail = await apiGet(`/api/v1/orchestrations/${encodeURIComponent(run.orchestration_id)}`);
            orchestrationResult.textContent = JSON.stringify(detail, null, 2);
            orchestrationResult.hidden = false;
            message.textContent = `Loaded ${run.orchestration_id}. Inspection is read-only; no retry was started.`;
          } catch (error) { message.textContent = `Could not inspect run: ${error.message}`; }
        });
        card.append(title, description, inspect);
        if (run.recovery_review) {
          const review = document.createElement("p"); review.className = "list-description";
          review.textContent = `Human review: ${run.recovery_review.disposition} · By ${run.recovery_review.reviewed_by || "unknown"} · ${run.recovery_review.reviewed_at || "time unavailable"}`;
          card.append(review);
        } else if (run.status === "uncertain") {
          const reviewControls = document.createElement("div"); reviewControls.className = "list-actions";
          for (const [disposition, label, confirmation] of [
            ["acknowledged_no_retry", "Acknowledge — no retry", "Record that you reviewed this uncertain run and will not retry it from this action?"],
            ["escalated_for_manual_investigation", "Escalate for investigation", "Record that this uncertain run needs manual investigation? This will not retry it."],
          ]) {
            const reviewButton = document.createElement("button"); reviewButton.type = "button"; reviewButton.className = "button secondary"; reviewButton.textContent = label;
            reviewButton.addEventListener("click", async () => {
              if (!globalThis.confirm(confirmation)) return;
              reviewButton.disabled = true; message.textContent = `Recording recovery review for ${run.orchestration_id}…`;
              try {
                await apiWrite(`/api/v1/orchestrations/${encodeURIComponent(run.orchestration_id)}/recovery-review`, { disposition });
                message.textContent = `Recovery review recorded for ${run.orchestration_id}. Task status is unchanged; no retry was started.`;
                await refreshOrchestrationHistory();
              } catch (error) {
                message.textContent = `Recovery review request returned: ${error.message}. Refresh and inspect the run before trying anything again.`;
                reviewButton.disabled = false;
              }
            });
            reviewControls.append(reviewButton);
          }
          card.append(reviewControls);
        }
        historyTarget.append(card);
      }
      message.textContent = "History loaded. Inspection is read-only; failed or uncertain tasks are not retried automatically.";
    } catch (error) {
      setText("orchestration-history-count", "Unavailable");
      empty(historyTarget, `Orchestration history unavailable: ${error.message}`);
      message.textContent = "Check that the connected API key has orchestration:read permission.";
    }
  }
  byId("orchestration-history-refresh").addEventListener("click", refreshOrchestrationHistory);

  const orchestrationForm = byId("orchestration-form");
  const orchestrationMessage = byId("orchestration-message");
  const orchestrationResult = byId("orchestration-result");
  let lastPayloadSignature = null;
  let lastPayloadIdempotencyKey = null;
  ["orchestration-objective", "orchestration-task-type", "orchestration-tool", "orchestration-tools", "orchestration-budget", "orchestration-mission-id", "orchestration-agent-ids", "orchestration-input"].forEach((id) => {
    byId(id).addEventListener("input", () => {
      // A changed request is a new intent; unchanged submissions reuse the same key.
      lastPayloadSignature = null;
      lastPayloadIdempotencyKey = null;
    });
  });
  orchestrationForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const objective = byId("orchestration-objective").value.trim();
    const taskType = byId("orchestration-task-type").value.trim();
    const tool = byId("orchestration-tool").value.trim();
    const requiredTools = byId("orchestration-tools").value.split(",").map((value) => value.trim()).filter(Boolean);
    const budget = Number(byId("orchestration-budget").value);
    const missionId = byId("orchestration-mission-id").value.trim();
    const candidateAgentIds = byId("orchestration-agent-ids").value.split(",").map((value) => value.trim()).filter(Boolean);
    let inputData = {};
    try { inputData = JSON.parse(byId("orchestration-input").value.trim() || "{}"); }
    catch (_) { orchestrationMessage.textContent = "Task input must be valid JSON."; return; }
    if (!inputData || typeof inputData !== "object" || Array.isArray(inputData)) {
      orchestrationMessage.textContent = "Task input must be a JSON object."; return;
    }
    if (!objective || !taskType || !tool || !requiredTools.length || !requiredTools.includes(tool) || !Number.isFinite(budget) || budget < 0) {
      orchestrationMessage.textContent = "Enter an objective, task type, valid non-negative budget, and required tools that include the execution tool."; return;
    }
    if (!byId("orchestration-confirm").checked) {
      orchestrationMessage.textContent = "Confirm the task submission checkbox before running."; return;
    }
    if (!globalThis.confirm("Run this internal agent task now? The runtime may update mission and task state. This is not a live provider call, and this action cannot be undone from this screen.")) return;
    const button = byId("orchestration-run-button");
    button.disabled = true;
    orchestrationMessage.textContent = "Submitting task to Alex…";
    orchestrationResult.hidden = true;
    const payload = {
      objective, task_type: taskType, required_tools: requiredTools, tool, budget,
      input: inputData, verification_required: true,
    };
    if (missionId) payload.mission_id = missionId;
    if (candidateAgentIds.length) payload.candidate_agent_ids = candidateAgentIds;
    const payloadSignature = JSON.stringify(payload);
    if (payloadSignature !== lastPayloadSignature || !lastPayloadIdempotencyKey) {
      lastPayloadSignature = payloadSignature;
      lastPayloadIdempotencyKey = `dashboard-${(globalThis.crypto && crypto.randomUUID) ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
    }
    payload.idempotency_key = lastPayloadIdempotencyKey;
    try {
      const data = await apiWrite("/api/v1/orchestrate", payload);
      orchestrationResult.textContent = JSON.stringify(data, null, 2);
      orchestrationResult.hidden = false;
      orchestrationMessage.textContent = `Task returned status: ${data.status || "unknown"}. Recovery state: ${data.recovery_state || "none"}. Selected agent: ${data.selected_agent_id || "unknown"}. Repeated unchanged submissions use the same idempotency key. No live external connector was called by this form.`;
      await refreshMissionList();
      if (data.mission_id) {
        byId("mission-lookup-id").value = data.mission_id;
        try { showMission(await apiGet(`/api/v1/missions/${encodeURIComponent(data.mission_id)}`)); } catch (_) { /* keep orchestration result visible if mission refresh is unavailable */ }
      }
    } catch (error) {
      orchestrationMessage.textContent = `Task submission failed: ${error.message}`;
    } finally { button.disabled = false; }
  });

  refreshAgentInventory();
  refreshOrchestrationHistory();
  refreshDashboard();
})();
