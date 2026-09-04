# Post-submission operations

After submitting ConvictionOS, preserve the exact release that was submitted and
continue improving the product without retroactively changing submitted claims.

## Before clicking submit

Run the release matrix that applies to the current repository:

```powershell
uv run pytest -q
uv run ruff check .
uv run mypy src/convictionos
uv run alembic upgrade head
uv run python scripts/check_architecture.py
uv run python scripts/check_claims.py --submission-mode
$sha = git rev-parse HEAD
uv run python scripts/build_submission.py --release-sha $sha
git diff --check
git status --short
```

For a local schema-only verification environment that intentionally does not
have the private competition paper-account identifier or production-style
database running, set `DATABASE_URL` to the disposable local test database and
`AGENT_ENABLED=false` for the Alembic command only. Do not use those overrides
for worker readiness or live paper acceptance.

If a separate frontend repository is used for the SaaS dashboard, also run its
lint, typecheck, test, build, browser smoke, and deployment verification before
adding the URL to the final form.

## Form verification

While authenticated, record the exact current form requirements before final
submit:

- Required text fields and character limits.
- Accepted video, image, deck, and repository link formats.
- Cutoff time and timezone.
- Whether edits are allowed after submit.
- Whether the Alpaca paper account ID is requested, and whether it is private.
- Whether the form asks for a public demo URL, public repo URL, social URL, or
  team/member fields.

Do not infer these from the public landing page. The public page confirms the
event framing, but the authenticated form is the source of truth for submission
fields.

## After submit

1. Fill `submission/submission-receipt.md`.
2. Open the submitted page from a logged-out browser context.
3. Click every public link.
4. Confirm video/deck permissions.
5. Confirm no credential, private account ID, or deployment variable is visible.
6. Tag or record the exact submitted SHA.
7. If edits are allowed, regenerate the submission manifest after every edit.
8. If edits are not allowed, continue product/P&L improvements as a new release
   and do not imply they were part of the submitted artifact.

## Ongoing paper operations

Keep the worker, reconciliation, incidents, backups, and credential rotation
running after submission. Treat new paper P&L evidence as post-submission unless
the form explicitly allows edits and the submitted claim ledger is updated.
