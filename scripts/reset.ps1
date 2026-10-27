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
    # flagd first. A service that starts while flagd is down keeps answering
    # with flag defaults: on 2026-10-26 a parallel restart left checkout blind
    # to paymentUnreachable and cart waiting 1.6 s per flag lookup.
    Write-Step "restarting flagd"
    docker compose @demoCompose restart flagd
    $deadline = (Get-Date).AddSeconds(60)
    while (-not (Test-TcpPort "127.0.0.1" 8013)) {
        if ((Get-Date) -gt $deadline) { throw "flagd did not listen on :8013 within 60 s" }
        Start-Sleep -Seconds 1
    }
    Write-Step "restarting the other demo containers"
    $others = docker compose @demoCompose config --services | Where-Object { $_ -ne "flagd" }
    docker compose @demoCompose restart @others
} else {
    Write-Step "reloading flagd"
    docker compose @demoCompose restart flagd
}

Write-Ok "lab reset (fault state cleared, data kept)"
