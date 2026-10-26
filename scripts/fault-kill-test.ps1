# Prove that killing the API mid-fault does not strand the lab.
#
#   scripts/fault-kill-test.ps1
#
# 1. inject a flag fault with a 45 s TTL,
# 2. kill the API process (not a graceful stop),
# 3. wait past the expiry with the API down,
# 4. restart the API and assert startup reconciliation reverted the flag,
# 5. inject a second fault, kill/restart again, assert it is still tracked and
#    can be reverted manually.
#
# The lab is left clean and the API is left running.

[CmdletBinding()]
param()

$ErrorActionPreference = "Continue"
. "$PSScriptRoot\_common.ps1"

$root = Get-DocockRoot
$api = Join-Path $root "apps\api"
$base = "http://127.0.0.1:8001"
$ofrep = "http://127.0.0.1:8016"
$failures = @()

function Get-ApiPid {
    $conn = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $conn) { return $null }
    return $conn.OwningProcess
}

function Wait-Api([int]$Seconds = 60) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri "$base/api/health" -UseBasicParsing -TimeoutSec 5
            if ($response.StatusCode -eq 200) { return $true }
        } catch { }
        Start-Sleep -Milliseconds 750
    }
    return $false
}

function Start-Api {
    Start-Process -FilePath "uv" `
        -ArgumentList "run", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8001" `
        -WorkingDirectory $api -WindowStyle Hidden | Out-Null
    return (Wait-Api -Seconds 60)
}

function Stop-Api {
    $processId = Get-ApiPid
    if ($null -eq $processId) { throw "no listener on 8001" }
    Stop-Process -Id $processId -Force
    Start-Sleep -Seconds 2
    if ($null -ne (Get-ApiPid)) { throw "API still listening after kill" }
    return $processId
}

function Get-FlagVariant([string]$Key) {
    $response = Invoke-RestMethod -Method Post -Uri "$ofrep/ofrep/v1/evaluate/flags/$Key" `
        -ContentType "application/json" -Body "{}"
    return $response.variant
}

Write-Step "preflight"
if (-not (Wait-Api -Seconds 30)) {
    Write-Fail "API is not answering on 8001; start it first"
    exit 1
}
Invoke-RestMethod -Method Post -Uri "$base/api/faults/reset-all?actor=kill-test" | Out-Null

# --- 1: expiry while the API is down ---------------------------------------
Write-Step "inject payment_failure with a 45 s TTL"
$body = @{
    fault_id = "payment_failure"
    params   = @{ variant = "100%" }
    ttl_s    = 45
    actor    = "kill-test"
} | ConvertTo-Json -Depth 4
$first = Invoke-RestMethod -Method Post -Uri "$base/api/faults" -ContentType "application/json" -Body $body
$firstId = $first.fault.id
$before = Get-FlagVariant "paymentFailure"
Write-Host "  fault $firstId status=$($first.fault.status); OFREP paymentFailure=$before"

Write-Step "kill the API"
$killed = Stop-Api
Write-Ok "killed pid $killed; flag stays $((Get-FlagVariant 'paymentFailure')) while the API is down"

Write-Step "wait past the expiry with the API down"
Start-Sleep -Seconds 55

Write-Step "restart the API"
if (-not (Start-Api)) {
    Write-Fail "API did not come back"
    exit 1
}
Start-Sleep -Seconds 3
$after = Invoke-RestMethod -Uri "$base/api/faults/$firstId"
$variant = Get-FlagVariant "paymentFailure"
if ($after.status -eq "EXPIRED") {
    Write-Ok "startup reconciliation expired fault $firstId"
} else {
    Write-Fail "fault $firstId is $($after.status), expected EXPIRED"
    $failures += "expiry-across-restart"
}
if ($variant -eq "off") {
    Write-Ok "paymentFailure was reverted by the restarted API"
} else {
    Write-Fail "paymentFailure reads $variant, expected off"
    $failures += "flag-not-reverted"
}

# --- 2: an unexpired fault survives a restart and is still revertible ------
Write-Step "inject cart_failure (ttl 300), kill and restart the API"
$body = @{
    fault_id = "cart_failure"
    params   = @{ variant = "10%" }
    ttl_s    = 300
    actor    = "kill-test"
} | ConvertTo-Json -Depth 4
$second = Invoke-RestMethod -Method Post -Uri "$base/api/faults" -ContentType "application/json" -Body $body
$secondId = $second.fault.id
$killed = Stop-Api
Start-Sleep -Seconds 3
if (-not (Start-Api)) {
    Write-Fail "API did not come back"
    exit 1
}
Start-Sleep -Seconds 3
$resumed = Invoke-RestMethod -Uri "$base/api/faults/$secondId"
if ($resumed.status -in @("PROPAGATING", "ACTIVE")) {
    Write-Ok "fault $secondId resumed as $($resumed.status)"
} else {
    Write-Fail "fault $secondId is $($resumed.status), expected PROPAGATING/ACTIVE"
    $failures += "resume"
}
$reverted = Invoke-RestMethod -Method Delete -Uri "$base/api/faults/${secondId}?actor=kill-test"
if ($reverted.status -eq "REVERTED") {
    Write-Ok "fault $secondId reverted after the restart"
} else {
    Write-Fail "fault $secondId is $($reverted.status), expected REVERTED"
    $failures += "revert-after-restart"
}
if ((Get-FlagVariant "cartFailure") -eq "off") {
    Write-Ok "cartFailure reads off"
} else {
    Write-Fail "cartFailure is not off"
    $failures += "cart-flag"
}

Write-Step "final state"
$open = (Invoke-RestMethod -Uri "$base/api/faults?status=open").open
$health = (Invoke-RestMethod -Uri "$base/api/health").status
Write-Host "  open faults=$open health=$health"
if ($open -ne 0) { $failures += "open-faults" }
if ($health -ne "ok") { $failures += "health" }

if ($failures.Count -gt 0) {
    Write-Fail ("failed: " + ($failures -join ", "))
    exit 1
}
Write-Ok "API kill mid-fault is safe: TTL reverted, unexpired fault resumed, lab clean"
exit 0
