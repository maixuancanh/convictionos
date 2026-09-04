const config = window.CONVICTIONOS_CONFIG ?? {};
const apiBaseUrl = config.apiBaseUrl ?? "";
const releaseSha = config.releaseSha ?? "unknown";

const runtimeState = document.querySelector("#runtime-state");
const runtimeDetail = document.querySelector("#runtime-detail");
const releaseShaElement = document.querySelector("#release-sha");
const paperDetail = document.querySelector("#paper-detail");
const strategyDetail = document.querySelector("#strategy-detail");
const workerState = document.querySelector("#worker-state");
const workerDetail = document.querySelector("#worker-detail");

releaseShaElement.textContent = releaseSha.slice(0, 12);

async function loadReadiness() {
  if (!apiBaseUrl) {
    runtimeState.textContent = "Demo-ready";
    runtimeDetail.textContent =
      "Set NEXT_PUBLIC_API_BASE_URL during build to connect live readiness.";
    workerState.textContent = "Demo";
    workerDetail.textContent = "Live worker status requires the production API.";
    return;
  }

  try {
    const response = await fetch(`${apiBaseUrl}/v1/public/competition-readiness`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(`Readiness returned ${response.status}`);
    }
    const data = await response.json();
    const outcome = data.outcome ?? data.status ?? "qualified";
    runtimeState.textContent = outcome[0].toUpperCase() + outcome.slice(1);
    runtimeDetail.textContent =
      data.public_reasons?.join("; ") ??
      data.summary ??
      "Public competition readiness loaded from ConvictionOS API.";
    paperDetail.textContent = data.paper_mode
      ? "Paper-only mode confirmed by the public readiness API."
      : "Paper-only state is not confirmed by the public readiness API.";
    strategyDetail.textContent = data.options_incorporated
      ? "Options strategy evidence is available in the readiness manifest."
      : "Options strategy is implemented, but production eligibility manifest is not loaded yet.";
  } catch (error) {
    runtimeState.textContent = "Unavailable";
    runtimeDetail.textContent =
      error instanceof Error
        ? `Readiness unavailable: ${error.message}`
        : "Readiness unavailable.";
  }
}

async function loadRuntimeStatus() {
  if (!apiBaseUrl) {
    return;
  }

  try {
    const response = await fetch(`${apiBaseUrl}/v1/runtime/status`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(`Runtime returned ${response.status}`);
    }
    const data = await response.json();
    workerState.textContent = data.worker_live ? "Live" : "Stale";
    const heartbeat = data.last_heartbeat_at
      ? new Date(data.last_heartbeat_at).toLocaleString()
      : "no heartbeat yet";
    const cycle = data.last_cycle_state ?? "no cycle yet";
    workerDetail.textContent = data.configured
      ? `${data.lease_owner_instance_id ?? "worker"} · heartbeat ${heartbeat} · ${cycle}`
      : "Runtime coordinator is not configured on the API.";
  } catch (error) {
    workerState.textContent = "Unavailable";
    workerDetail.textContent =
      error instanceof Error
        ? `Runtime status unavailable: ${error.message}`
        : "Runtime status unavailable.";
  }
}

loadReadiness();
loadRuntimeStatus();
