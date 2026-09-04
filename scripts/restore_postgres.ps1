param(
  [Parameter(Mandatory = $true)]
  [string]$BackupFile,

  [Parameter(Mandatory = $true)]
  [string]$TargetConnectionString,

  [Parameter(Mandatory = $true)]
  [string]$TargetDatabase,

  [Parameter(Mandatory = $true)]
  [string]$ConfirmRestoreTarget
)

$ErrorActionPreference = "Stop"

if ($ConfirmRestoreTarget -ne $TargetDatabase) {
  throw "ConfirmRestoreTarget must equal TargetDatabase"
}

if (-not (Test-Path -LiteralPath $BackupFile)) {
  throw "BackupFile does not exist"
}

Write-Output "Restoring backup into isolated validation database '$TargetDatabase'"
Write-Output "Backup file: $BackupFile"

pg_restore `
  --no-owner `
  --no-acl `
  --dbname "$TargetConnectionString" `
  "$BackupFile"

Write-Output "Restore completed for isolated database '$TargetDatabase'"
