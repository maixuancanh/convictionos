# Backup and Restore

Backups must use `pg_dump --format=custom --no-owner --no-acl` against a
read-only production connection string. Do not print connection strings or
commit dumps.

Restore only into an isolated validation database:

```powershell
powershell -File scripts/restore_postgres.ps1 `
  -BackupFile artifacts/backup/latest.dump `
  -TargetConnectionString "<validation-db-url>" `
  -TargetDatabase convictionos_restore_test `
  -ConfirmRestoreTarget convictionos_restore_test
```

After restore, run:

```powershell
uv run python scripts/verify_restore.py --database-url "<validation-db-url>" --mutation-disabled
```
