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
  await Promise.all([loadReadiness(), loadRuntimeStatus()]);
  refreshButton.disabled = false;
  refreshButton.removeAttribute("aria-busy");
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
refreshStatus();
