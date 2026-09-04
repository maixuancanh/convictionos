param(
  [Parameter(Mandatory = $true)]
  [ValidatePattern("^[0-9a-f]{40}$")]
  [string]$GitSha,

  [switch]$DryRun,

  [string]$ArtifactRoot = "artifacts/releases"
)

$ErrorActionPreference = "Stop"

$releaseDir = Join-Path $ArtifactRoot $GitSha
New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null

$steps = @("test", "migrate", "api", "worker", "verify")

if (-not $DryRun) {
  $status = git status --porcelain
  if ($status) {
    throw "Tracked worktree must be clean before deploy"
  }

  uv run pytest -q
  uv run python scripts/check_architecture.py
  uv run python scripts/check_claims.py --allow-missing-public-before-first-release
  python -m convictionos.commands migrate
  railway up --config railway.api.toml
  railway up --config railway.worker.toml
}

$manifest = [ordered]@{
  release_sha = $GitSha
  dry_run = [bool]$DryRun
  api_url = "pending"
  worker_public = $false
  migration_public = $false
  steps = $steps
}

$manifestPath = Join-Path $releaseDir "release.json"
$json = $manifest | ConvertTo-Json -Depth 10
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
$resolvedManifestPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($manifestPath)
[System.IO.File]::WriteAllText($resolvedManifestPath, $json, $utf8NoBom)
Write-Output "Release manifest written: $manifestPath"
