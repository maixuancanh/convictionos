# Authorized Alpaca paper smoke checklist

Run this checklist only after explicit operator approval. It does not authorize
live trading and must use an Alpaca Paper account.

- Confirm endpoint `https://paper-api.alpaca.markets/v2`, paper account, and
  options level 3.
- Run `python scripts/verify_alpaca_cli.py --offline` and inspect the sanitized
  JSON report before any authorized paper order.
- Run `alpaca order submit --schema` as a dry-run schema gate only. Do not submit
  an order from the CLI outside the application execution path.
- Reconcile an existing exact two-leg opening spread; verify one managed public
  position and no private identifiers in `/v1/positions`.
- Observe a HOLD cycle with fresh evidence before enabling a close test.
- During market hours, authorize only a tiny close of the existing spread.
  Verify one stable client order ID and no duplicate submission.
- Confirm exact parent fill and both option legs absent before
  `CLOSED_RECONCILED`.
- Wait for delayed option activities and recognized fees. Until fee evidence
  arrives, verified fees and after-cost P&L must remain `null`.

Do not infer profitability from this smoke. Paper fills are execution-plumbing
evidence, not proof of live liquidity or performance. Never exercise, roll,
assign, or alter thresholds from this checklist.
