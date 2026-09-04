# Trust-to-Receipt Demo Runbook

The default demo uses `FakeBroker` and never contacts Alpaca.

1. Start PostgreSQL: `docker compose up -d db`.
   If Docker Desktop is unavailable, start the local PostgreSQL service in WSL and
   set `DATABASE_URL` to its reachable host and port before applying the schema.
2. Apply schema: `uv run alembic upgrade head`.
3. Start the API: `uv run uvicorn convictionos.api.main:app --factory`.
4. Run `uv run python scripts/run_demo.py`.
5. Confirm one intent, one consumed reservation, one broker order, and one receipt.
6. Run the mutation endpoint:
   `Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/demo/evaluate-mutation -ContentType application/json -Body '{"quantity":"20","limit_price":"1.25"}'`.
7. Confirm the response contains `max_trade_loss_exceeded` and `max_notional_exceeded`.
8. Open `http://127.0.0.1:8000/` and select **Open Mission Control**.
9. The dashboard is paper-safe: `Run fake demo` creates one deterministic fake-broker receipt; it never submits a live order.

For Alpaca paper, obtain two currently active same-underlying option contracts from the
Alpaca option contracts API, confirm options level 3, use a debit limit no greater than the
approved demo risk, and set `BROKER_MODE=alpaca_paper`. Never put credentials or OAuth
tokens in source control. Paper fills do not demonstrate live liquidity or profitability.

Stop after paper verification. This runbook does not authorize live trading.

## Lifecycle verification

The scheduler always reconciles lifecycle state before entry. During a closed
US session it may poll positions and activities but it does not submit a close
or opening order. During an open session, a close requires a fresh exact option
quote snapshot and a matching `exit-policy-v1` decision; the reverse legs must
retain the original symbols, ratios, and whole-spread quantity.

Review states are fail-closed: assignment (`OPASN`), expiration (`OPEXP` or
`OPXRC`), asymmetric legs, unexplained disappearance, and broker/hash mismatch
block new entries. Missing fee activities stay pending; the system never
creates a zero fee or synthetic after-cost result. Verify public state with
`GET /v1/positions`; private account, intent, broker-order, and activity IDs
must not appear in its JSON or Mission Control HTML.
