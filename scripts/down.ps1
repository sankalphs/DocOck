# Stops the lab.
#
#   scripts/down.ps1              # stop containers, keep data
#   scripts/down.ps1 -Volumes     # also delete the Postgres volume

[CmdletBinding()]
param([switch]$Volumes)

. "$PSScriptRoot\_common.ps1"

$root = Get-DocockRoot
$demo = Join-Path $root "opentelemetry-demo"
$env:DOCOCK_ROOT = $root

if (Test-Path (Join-Path $demo "compose.yaml")) {
    Write-Step "stopping demo + observability"
    $demoCompose = @(
        "--project-directory", $demo,
        "-p", "docock-demo",
        "-f", (Join-Path $demo "compose.yaml"),
        "-f", (Join-Path $demo "compose.observability.yaml"),
        "-f", (Join-Path $root "infra\compose\lab.override.yaml")
    )
    docker compose @demoCompose down --remove-orphans
}

Write-Step "stopping lab services"
$downArgs = @("down", "--remove-orphans")
if ($Volumes) { $downArgs += "-v" }
docker compose -p docock-lab --env-file (Join-Path $root ".env") -f (Join-Path $root "infra\compose\base.yaml") @downArgs

Write-Ok "stack stopped"
