# Release and Rollback

Rollback starts by pausing new entries while preserving reconciliation and
closing-only workflows. Deploy the previous immutable API, worker, and web
revision only after confirming schema compatibility.

Never downgrade a migration after newer code has written irreversible data.
