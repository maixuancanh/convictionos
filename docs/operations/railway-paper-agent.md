# Railway paper-agent rollout

1. Back up the managed PostgreSQL database.
2. Configure `BROKER_MODE=alpaca_paper`, Alpaca paper credentials, an AI
   provider key, `AGENT_ENABLED=true`, and a long `AGENT_CONTROL_TOKEN` in the
   Railway secret manager. Never commit these values.
3. Deploy one worker instance first and run `alembic upgrade head` through the
   release command. Do not run a downgrade while lifecycle rows exist.
4. Run `uv run python scripts/competition_smoke.py --phase offline` first. Then
   verify `/health`, `/v1/agent/status`, `/v1/positions`,
   `/v1/public/competition-readiness`, and the dashboard. The public route must
   not contain the account ID, balances, credentials, control token, or order
   IDs. Inspect the private eligibility route only with the control token.
5. Confirm the eligibility manifest is current and eligible, the Alpaca clock
   supplied the next open/close, lifecycle reconciliation runs before entry,
   and review states block new entries.
6. Scale only after the first paper cycle is observed. Keep one scheduler
   owner per database unless an external distributed scheduler is configured;
   the database claim/version guard protects duplicate cycles.

`authorized-paper` smoke is deliberately fail-closed until an eligible,
immutable intent ID is persisted. Never use a screenshot or manual flag to
override that gate.

Rollback means stopping the worker, restoring the application revision, and
preserving lifecycle rows for manual reconciliation. Do not delete or rewrite
position transitions to make a rollback appear clean.
## Immutable Roles

Railway should run three services from the same image:

- `convictionos-migrate` with `railway.migrate.toml`
- `convictionos-api` with `railway.api.toml`
- `convictionos-worker` with `railway.worker.toml`

Expose only the API service publicly. The worker and migration roles must not
receive public domains.
