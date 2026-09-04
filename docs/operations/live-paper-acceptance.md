# Live Paper Acceptance

Read-only acceptance is safe to run against production:

```powershell
uv run python scripts/live_paper_acceptance.py `
  --phase read-only `
  --base-url $env:CONVICTIONOS_PRODUCTION_URL
```

The mutation phase is fail-closed and requires all guards:
`--authorize-paper-mutation`, `--eligible-manifest-hash`,
`--authorized-intent-id`, `--expected-account-fingerprint`, and
`--confirm PAPER_ONLY`.

The script never creates or edits strategy candidates or intents.
