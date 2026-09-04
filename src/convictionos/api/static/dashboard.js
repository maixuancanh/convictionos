(() => {
  async function hydrateLifecycle() {
    const panel = document.querySelector('.managed-positions-panel');
    if (!panel) return;
    try {
      const response = await fetch('/v1/positions', {headers: {Accept: 'application/json'}});
      if (!response.ok) throw new Error('positions unavailable');
      const data = await response.json();
      panel.querySelectorAll('[data-lifecycle-count]').forEach((node) => {
        node.textContent = String(data[node.dataset.lifecycleCount] ?? 0);
      });
      const list = panel.querySelector('#managed-positions-list');
      if (!list) return;
      list.replaceChildren();
      if (!data.positions || data.positions.length === 0) {
        const empty = document.createElement('p');
        empty.className = 'empty';
        empty.textContent = 'No reconciled managed positions.';
        list.append(empty);
        return;
      }
      data.positions.forEach((position) => {
        const row = document.createElement('div');
        row.className = 'managed-position-row';
        row.textContent = `${position.underlying} · ${position.lifecycle_state.replaceAll('_', ' ')} · ${position.quote_freshness}`;
        list.append(row);
      });
    } catch (_) {
      const list = panel.querySelector('#managed-positions-list');
      if (list) list.replaceChildren();
      const error = document.createElement('p');
      error.className = 'empty';
      error.textContent = 'Lifecycle data temporarily unavailable.';
      list?.append(error);
    }
  }
  hydrateLifecycle();
  const schedulerResult = document.querySelector('#scheduler-result');
  const schedulerToken = document.querySelector('#agent-control-token');
  document.querySelectorAll('[data-scheduler-action]').forEach((button) => {
    button.addEventListener('click', async () => {
      if (!schedulerResult || !schedulerToken) return;
      const action = button.dataset.schedulerAction;
      schedulerResult.textContent = 'Contacting paper agent…';
      try {
        const response = await fetch(`/v1/agent/scheduler/${action}`, {
          method: 'POST', headers: {'X-Agent-Control-Token': schedulerToken.value}
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Control failed');
        schedulerResult.textContent = `Scheduler ${action}: ${data.last_run_state || (data.scheduler_paused ? 'paused' : 'ready')}. Refresh to see history.`;
        if (action === 'run-now') window.location.reload();
      } catch (error) {
        schedulerResult.textContent = String(error.message || 'Control plane unavailable.');
      }
    });
  });
  const form = document.querySelector('#mutation-form');
  const result = document.querySelector('#mutation-result');
  if (!form || !result) return;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    result.textContent = 'Evaluating deterministic policy mutation…';
    const body = Object.fromEntries(new FormData(form).entries());
    try {
      const response = await fetch('/v1/demo/evaluate-mutation', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
      });
      const data = await response.json();
      const reasons = (data.reasons || []).join(', ') || 'No policy exceptions';
      result.textContent = `${String(data.outcome).toUpperCase()}: ${reasons}. Operation hash changed: ${data.operation_hash_changed}. Simulation only — no order was submitted.`;
    } catch (error) {
      result.textContent = 'Control plane unavailable. Simulation only — no order was submitted.';
    }
  });
})();
