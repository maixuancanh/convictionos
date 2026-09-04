param(
  [Parameter(Mandatory = $true)]
  [string]$Environment,

  [Parameter(Mandatory = $true)]
  [string]$ConnectionString,

  [string]$OutputDirectory = "artifacts/backup"
)

$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupFile = Join-Path $OutputDirectory "convictionos-$Environment-$timestamp.dump"

Write-Output "Creating PostgreSQL backup for environment '$Environment'"
Write-Output "Backup target: $backupFile"

pg_dump `
  --format=custom `
  --no-owner `
  --no-acl `
  --file "$backupFile" `
  "$ConnectionString"

Write-Output "Backup completed: $backupFile"
