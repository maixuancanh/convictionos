# Railway

Use one immutable image with three services:

- `convictionos-migrate`: `python -m convictionos.commands migrate`
- `convictionos-api`: `python -m convictionos.commands api --host 0.0.0.0 --port ${PORT:-8000}`
- `convictionos-worker`: `python -m convictionos.commands worker`

Only the API service should receive a public domain. Migration and worker
services must remain private.
