# Pins the upstream OpenTelemetry demo checkout.
#
# The demo is intentionally NOT committed to this repository. This script makes
# the checkout reproducible: same commit, same compose files, every time.
#
#   scripts/bootstrap-demo.ps1              # clone if missing
#   scripts/bootstrap-demo.ps1 -Force       # re-clone from scratch

[CmdletBinding()]
param(
    [string]$Ref = "5c8d404a85380226ff45a39dcb4159eda9552c56",
    [string]$Repo = "https://github.com/open-telemetry/opentelemetry-demo.git",
    [switch]$Force
)

. "$PSScriptRoot\_common.ps1"

$root = Get-DocockRoot
$demo = Join-Path $root "opentelemetry-demo"
$lock = Join-Path $root "infra\otel-demo.lock.json"

if ((Test-Path $demo) -and -not $Force) {
    Write-Ok "demo checkout already present at $demo"
    Write-Host "     (use -Force to re-clone at $Ref)"
    exit 0
}

if ($Force -and (Test-Path $demo)) {
    Write-Step "removing existing checkout"
    Remove-Item -Recurse -Force $demo
}

Write-Step "cloning $Repo"
git clone --depth 1 $Repo $demo
if ($LASTEXITCODE -ne 0) { throw "git clone failed" }

Write-Step "checking out pinned commit $Ref"
git -C $demo fetch --depth 1 origin $Ref
if ($LASTEXITCODE -eq 0) {
    git -C $demo checkout --detach FETCH_HEAD
} else {
    Write-Warn "could not fetch pinned commit; staying on default branch"
    $Ref = (git -C $demo rev-parse HEAD).Trim()
}

$payload = [ordered]@{
    repository = $Repo
    commit     = $Ref
    fetched_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
}
New-Item -ItemType Directory -Force (Split-Path -Parent $lock) | Out-Null
$payload | ConvertTo-Json | Set-Content -Encoding utf8 $lock

Write-Ok "demo pinned at $Ref"
Write-Host "     lock file: $lock"
