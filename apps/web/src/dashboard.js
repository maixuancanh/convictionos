const config = window.CONVICTIONOS_CONFIG ?? {};
const publicApiFallback = "https://divine-illumination-production-7f99.up.railway.app";
const apiBaseUrl = (config.apiBaseUrl || publicApiFallback).replace(/\/$/, "");
const releaseSha = config.releaseSha ?? "unknown";

const get = (selector) => document.querySelector(selector);
const runtimeState = get("#runtime-state");
const runtimeDetail = get("#runtime-detail");
const releaseShaElement = get("#release-sha");
const paperMode = get("#paper-mode");
const strategyDetail = get("#strategy-detail");
const workerState = get("#worker-state");
const workerDetail = get("#worker-detail");
const workerInline = get("#worker-inline");
const nextCycle = get("#next-cycle");
const notice = get("#status-notice");
const noticeTitle = get("#notice-title");
const noticeDetail = get("#notice-detail");
const agentBadge = get("#agent-badge");
const navAgentDot = get("#nav-agent-dot");
const brokerHealth = get("#broker-health");
const brokerHealthState = get("#broker-health-state");
const refreshButton = get("#refresh-status");
const runAgentButton = get("#run-agent-now");
const controlDialog = get("#agent-control-dialog");
const controlForm = get("#agent-control-form");
const controlToken = get("#control-token");
const actionFeedback = get("#action-feedback");
const confirmRunButton = get("#confirm-run-agent");
const lastRun = get("#last-run");
const decisionTable = get("#decision-table");
const positionSummary = get("#position-summary");
const positionList = get("#position-list");

function humanize(value) {
  if (!value) return "Unknown";
  return String(value)
    .replaceAll("_", " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function compactDate(value) {
  if (!value) return "No heartbeat recorded";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Heartbeat time unavailable";
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function setAgentBadge(label, state) {
  agentBadge.className = `state-badge ${state}`.trim();
  agentBadge.innerHTML = "";
  const dot = document.createElement("span");
  agentBadge.append(dot, document.createTextNode(label));
}

function showNotice(kind, title, detail) {
  notice.className = `notice-banner ${kind}`.trim();
  noticeTitle.textContent = title;
  noticeDetail.textContent = detail;
}

async function fetchJson(path) {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new Error(`API returned ${response.status}`);
  return response.json();
}

async function postControl(path, token) {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "X-Agent-Control-Token": token,
    },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body.detail === "string" ? body.detail : `API returned ${response.status}`;
    throw new Error(detail);
  }
  return body;
}

function appendCell(row, value, className = "") {
  const cell = document.createElement("td");
  cell.textContent = value;
  if (className) cell.className = className;
  row.append(cell);
}

function renderDecision(decision, fallback = {}) {
  decisionTable.innerHTML = "";
  if (!decision) {
    const row = document.createElement("tr");
    row.className = "empty-row";
    const cell = document.createElement("td");
    cell.colSpan = 5;
    const empty = document.createElement("div");
    empty.className = "empty-state compact-empty";
    const title = document.createElement("strong");
    title.textContent = "No production decision recorded yet";
    const copy = document.createElement("p");
    copy.textContent = "Run a paper cycle to create an approved, denied, or abstained audit record.";
    empty.append(title, copy);
    cell.append(empty);
    row.append(cell);
    decisionTable.append(row);
    return;
  }

  const row = document.createElement("tr");
  const outcome = decision.outcome ?? decision.state ?? fallback.state ?? "recorded";
  const mandate = decision.mandate_id ?? fallback.mandate_id ?? "Current";
  const instrument = decision.instrument ?? decision.symbol ?? fallback.underlying ?? "SPY";
  const reasons = decision.reasons ?? fallback.reasons ?? [];
  const reason = Array.isArray(reasons) ? reasons[0] : reasons;
  const receipt = decision.receipt_hash ?? fallback.receipt?.integrity_hash ?? fallback.receipt_hash;
  appendCell(row, humanize(outcome), `decision-outcome ${String(outcome).toLowerCase()}`);
  appendCell(row, String(mandate));
  appendCell(row, String(instrument));
  appendCell(row, reason || "Decision recorded by the policy engine");
  appendCell(row, receipt ? String(receipt).slice(0, 12) : "Pending");
  decisionTable.append(row);
}

function renderPositions(data) {
  const positions = Array.isArray(data.positions) ? data.positions : [];
  positionSummary.textContent = `${data.open ?? 0} open · ${data.review ?? 0} review`;
  positionList.innerHTML = "";
  if (!positions.length) {
    const empty = document.createElement("div");
    empty.className = "evidence-empty";
    const title = document.createElement("strong");
    title.textContent = "No reconciled paper positions";
    const copy = document.createElement("p");
    copy.textContent = "The broker lifecycle is connected; positions will appear after an eligible paper fill.";
    empty.append(title, copy);
    positionList.append(empty);
    return;
  }

  positions.forEach((position) => {
    const item = document.createElement("article");
    item.className = "position-item";
    const identity = document.createElement("div");
    const symbol = document.createElement("strong");
    symbol.textContent = `${position.underlying ?? "SPY"} · ${humanize(position.horizon)}`;
    const detail = document.createElement("small");
    detail.textContent = `${humanize(position.direction)} · ${position.expiry_dte ?? "?"} DTE`;
    identity.append(symbol, detail);
    const state = document.createElement("span");
    state.className = "soft-badge";
    state.textContent = humanize(position.lifecycle_state);
    item.append(identity, state);
    positionList.append(item);
  });
}

async function loadControlPlane() {
  try {
    const data = await fetchJson("/v1/control-plane");
    const latest = data.latest_decision;
    renderDecision(latest?.decision ?? latest, { ...data, ...(latest ?? {}) });
    get("#underlying").textContent = `${data.underlying ?? "SPY"} options`;
  } catch {
    renderDecision(null);
  }
}

async function loadPositions() {
  try {
    renderPositions(await fetchJson("/v1/positions"));
  } catch (error) {
    positionSummary.textContent = "Unavailable";
    positionList.textContent = error instanceof Error ? error.message : "Position lifecycle unavailable";
  }
}

async function loadAgentStatus() {
  try {
    const data = await fetchJson("/v1/agent/status");
    lastRun.textContent = data.last_run_at
      ? `${compactDate(data.last_run_at)} · ${humanize(data.last_run_state)}`
      : "No API run recorded";
    runAgentButton.disabled = !data.enabled || !data.configured;
    runAgentButton.title = runAgentButton.disabled ? "Paper agent is not configured" : "Run one protected paper cycle";
  } catch (error) {
    lastRun.textContent = "Agent status unavailable";
    runAgentButton.disabled = true;
  }
}

async function loadReadiness() {
  try {
    const data = await fetchJson("/v1/public/competition-readiness");
    const outcome = String(data.outcome ?? data.status ?? "unavailable");
    const eligible = outcome.toLowerCase() === "eligible";

    runtimeState.textContent = humanize(outcome);
    runtimeDetail.textContent = eligible ? "Competition checks satisfied" : "Readiness needs attention";
    paperMode.textContent = data.paper_mode ? "Paper only" : "Unconfirmed";
    strategyDetail.textContent = data.options_incorporated
      ? `${String(data.options_feed ?? "unknown").toUpperCase()} feed · options enabled`
      : "Options eligibility not confirmed";
    releaseShaElement.textContent = String(data.deployed_sha ?? releaseSha).slice(0, 8);
    brokerHealth.textContent = data.paper_mode
      ? "Paper environment confirmed by public readiness"
      : "Paper environment is not confirmed";
    brokerHealthState.textContent = data.paper_mode ? "Confirmed" : "Attention";
    setAgentBadge(eligible ? "Eligible" : humanize(outcome), eligible ? "ready" : "error");
    showNotice(
      eligible ? "ready" : "error",
      eligible ? "Paper runtime is competition eligible" : "Production readiness needs attention",
      eligible
        ? "Options, mandate, market-data capability, and paper-mode checks are present."
        : data.public_reasons?.join(" · ") || "Open the readiness endpoint for details.",
    );
  } catch (error) {
    runtimeState.textContent = "Unavailable";
    runtimeDetail.textContent = "Readiness API could not be reached";
    brokerHealth.textContent = "Public readiness is unavailable";
    brokerHealthState.textContent = "Unavailable";
    setAgentBadge("Unavailable", "error");
    showNotice("error", "Readiness is temporarily unavailable", error instanceof Error ? error.message : "Try again shortly.");
  }
}

async function loadRuntimeStatus() {
  try {
    const data = await fetchJson("/v1/runtime/status");
    const live = Boolean(data.worker_live);
    const heartbeat = compactDate(data.last_heartbeat_at);
    const cycle = humanize(data.last_cycle_state ?? "No cycle yet");

    workerState.textContent = live ? "Live" : "Stale";
    workerDetail.textContent = `${heartbeat} · ${cycle}`;
    workerInline.textContent = live ? `Worker live · ${cycle}` : `Worker stale · ${heartbeat}`;
    nextCycle.textContent = data.effective_control_state === "running" ? `Running · last ${cycle.toLowerCase()}` : humanize(data.effective_control_state);
    navAgentDot.classList.toggle("live", live);
  } catch (error) {
    workerState.textContent = "Unavailable";
    workerDetail.textContent = error instanceof Error ? error.message : "Runtime API could not be reached";
    workerInline.textContent = "Worker status unavailable";
    nextCycle.textContent = "Runtime unavailable";
    navAgentDot.classList.remove("live");
  }
}

async function refreshStatus() {
  refreshButton.disabled = true;
  refreshButton.setAttribute("aria-busy", "true");
  await Promise.all([loadReadiness(), loadRuntimeStatus(), loadControlPlane(), loadPositions(), loadAgentStatus()]);
  refreshButton.disabled = false;
  refreshButton.removeAttribute("aria-busy");
}

function closeControlDialog() {
  controlToken.value = "";
  actionFeedback.className = "action-feedback";
  actionFeedback.textContent = "No order is submitted unless deterministic eligibility and risk gates pass.";
  controlDialog.close();
}

function setupAgentControl() {
  runAgentButton.addEventListener("click", () => {
    controlDialog.showModal();
    controlToken.focus();
  });
  get("#close-control-dialog").addEventListener("click", closeControlDialog);
  get("#cancel-control-action").addEventListener("click", closeControlDialog);
  controlDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeControlDialog();
  });
  controlDialog.addEventListener("click", (event) => {
    if (event.target === controlDialog) closeControlDialog();
  });
  controlForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const token = controlToken.value.trim();
    if (!token) return;
    confirmRunButton.disabled = true;
    actionFeedback.className = "action-feedback working";
    actionFeedback.textContent = "Running one protected paper cycle…";
    try {
      const result = await postControl("/v1/agent/scheduler/run-now", token);
      const state = result.state ?? result.entry?.state ?? "completed";
      actionFeedback.className = "action-feedback success";
      actionFeedback.textContent = `Cycle ${humanize(state).toLowerCase()}. Refreshing production evidence…`;
      controlToken.value = "";
      await refreshStatus();
      window.setTimeout(closeControlDialog, 900);
    } catch (error) {
      controlToken.value = "";
      actionFeedback.className = "action-feedback error";
      actionFeedback.textContent = error instanceof Error ? error.message : "The paper cycle could not be started.";
      controlToken.focus();
    } finally {
      confirmRunButton.disabled = false;
    }
  });
}

function setupNavigation() {
  const sidebar = get("#app-sidebar");
  const overlay = get("#sidebar-overlay");
  const toggle = get("#nav-toggle");
  const close = get("#nav-close");

  const setOpen = (open) => {
    sidebar.classList.toggle("open", open);
    overlay.classList.toggle("open", open);
    toggle.setAttribute("aria-expanded", String(open));
    document.body.style.overflow = open ? "hidden" : "";
  };

  toggle.addEventListener("click", () => setOpen(true));
  close.addEventListener("click", () => setOpen(false));
  overlay.addEventListener("click", () => setOpen(false));
  sidebar.querySelectorAll("a[href^='#']").forEach((link) => link.addEventListener("click", () => setOpen(false)));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setOpen(false);
  });
}

releaseShaElement.textContent = String(releaseSha).slice(0, 8);
refreshButton.addEventListener("click", refreshStatus);
setupNavigation();
setupAgentControl();
refreshStatus();
