# Resets the lab to a known-good state.
#
# Default: restore the runtime flag file from upstream and reload flagd.
# -Hard:   additionally restart every demo container.
#
#   scripts/reset.ps1
#   scripts/reset.ps1 -Hard

[CmdletBinding()]
param([switch]$Hard)

. "$PSScriptRoot\_common.ps1"

$root = Get-DocockRoot
$demo = Join-Path $root "opentelemetry-demo"
$runtimeFlagd = Join-Path $root "runtime\flagd"
$seedFlagFile = Join-Path $demo "src\flagd\demo.flagd.json"
$targetFlagFile = Join-Path $runtimeFlagd "demo.flagd.json"
$env:DOCOCK_ROOT = $root

if (-not (Test-Path $demo)) { throw "demo checkout not found; run scripts/bootstrap-demo.ps1" }

Write-Step "restoring feature flags from upstream"
New-Item -ItemType Directory -Force $runtimeFlagd | Out-Null
Copy-FlagSeed -Source $seedFlagFile -Destination $targetFlagFile

$demoCompose = @(
    "--project-directory", $demo,
    "-p", "docock-demo",
    "-f", (Join-Path $demo "compose.yaml"),
    "-f", (Join-Path $demo "compose.observability.yaml"),
    "-f", (Join-Path $root "infra\compose\lab.override.yaml")
)

if ($Hard) {
    Write-Step "restarting demo containers"
    docker compose @demoCompose restart
} else {
    Write-Step "reloading flagd"
    docker compose @demoCompose restart flagd
}

Write-Ok "lab reset (fault state cleared, data kept)"
