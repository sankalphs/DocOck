# Verifies the local stack is reachable.
#
#   scripts/health.ps1
#   scripts/health.ps1 -WaitSeconds 180
#   scripts/health.ps1 -Json

[CmdletBinding()]
param(
    [int]$WaitSeconds = 0,
    [switch]$Json
)

. "$PSScriptRoot\_common.ps1"

$deadline = (Get-Date).AddSeconds($WaitSeconds)
do {
    $results = Test-StackHealth
    $failed = @($results | Where-Object { -not $_.Ok })
    if ($failed.Count -eq 0) { break }
    if ($WaitSeconds -le 0) { break }
    Write-Host "waiting for $($failed.Count) check(s): $(($failed | ForEach-Object { $_.Name }) -join ', ')"
    Start-Sleep -Seconds 5
} while ((Get-Date) -lt $deadline)

if ($Json) {
    $results | ConvertTo-Json -Depth 4
} else {
    Show-HealthTable $results
}

if (@($results | Where-Object { -not $_.Ok }).Count -gt 0) { exit 1 }
exit 0
