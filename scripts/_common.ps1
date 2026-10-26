# Shared helpers for DocOck PowerShell scripts.
# Windows PowerShell 5.1 compatible: no ternary, no null-coalescing.

$ErrorActionPreference = "Stop"

function Get-DocockRoot {
    return (Split-Path -Parent $PSScriptRoot)
}

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}

# flagd ignores a flag file whose mtime moved backwards (Copy-Item preserves the
# source timestamp), and rejects a UTF-8 BOM. Always seed through this helper.
function Copy-FlagSeed {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    Copy-Item -Force $Source $Destination
    (Get-Item $Destination).LastWriteTime = Get-Date
}

function Write-Ok([string]$Message) {
    Write-Host "  OK   $Message" -ForegroundColor Green
}

function Write-Warn([string]$Message) {
    Write-Host "  WARN $Message" -ForegroundColor Yellow
}

function Write-Fail([string]$Message) {
    Write-Host "  FAIL $Message" -ForegroundColor Red
}

function Test-TcpPort([string]$TargetHost, [int]$Port, [int]$TimeoutMs = 1000) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect($TargetHost, $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-FreePortState([int[]]$Ports) {
    $state = @{}
    foreach ($p in $Ports) {
        $state[$p] = -not (Test-TcpPort -TargetHost "127.0.0.1" -Port $p)
    }
    return $state
}

function Invoke-HttpProbe {
    param(
        [string]$Name,
        [string]$Url,
        [int[]]$AcceptStatus = @(200),
        [int]$TimeoutSec = 5,
        [string]$BodyMatch = ""
    )
    $result = [pscustomobject]@{
        Name   = $Name
        Url    = $Url
        Ok     = $false
        Status = 0
        Detail = ""
    }
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec -ErrorAction Stop
        $result.Status = [int]$response.StatusCode
        if ($AcceptStatus -notcontains $result.Status) {
            $result.Detail = "unexpected status $($result.Status)"
            return $result
        }
        if ($BodyMatch -ne "" -and $response.Content -notmatch $BodyMatch) {
            $result.Detail = "body did not match /$BodyMatch/"
            return $result
        }
        $result.Ok = $true
        $result.Detail = "HTTP $($result.Status)"
    } catch {
        $status = 0
        if ($_.Exception.Response) {
            $status = [int]$_.Exception.Response.StatusCode
        }
        $result.Status = $status
        $result.Detail = $_.Exception.Message
    }
    return $result
}

function Test-StackHealth {
    param(
        [string[]]$Skip = @()
    )
    $checks = @(
        @{ Name = "demo frontend";      Url = "http://127.0.0.1:8080/";                       Match = "" },
        @{ Name = "prometheus ready";   Url = "http://127.0.0.1:9090/-/ready";                Match = "Ready" },
        @{ Name = "prometheus spans";   Url = "http://127.0.0.1:9090/api/v1/query?query=count(traces_span_metrics_calls_total)"; Match = '"result":\[\{' },
        @{ Name = "jaeger api";         Url = "http://127.0.0.1:16686/jaeger/ui/api/v3/services"; Match = '"services":\[' },
        @{ Name = "opensearch cluster"; Url = "http://127.0.0.1:9200/_cluster/health";        Match = '"status":"(green|yellow)"' },
        @{ Name = "opensearch logs";    Url = "http://127.0.0.1:9200/_cat/indices/otel-logs*?format=json"; Match = "otel-logs-" },
        @{ Name = "flagd ui";           Url = "http://127.0.0.1:4000/";                       Match = "" },
        @{ Name = "load generator";     Url = "http://127.0.0.1:8089/";                       Match = "" }
    )

    $results = @()
    foreach ($c in $checks) {
        if ($Skip -contains $c.Name) { continue }
        $results += Invoke-HttpProbe -Name $c.Name -Url $c.Url -BodyMatch $c.Match
    }

    $pgOk = Test-TcpPort -TargetHost "127.0.0.1" -Port 5433
    $results += [pscustomobject]@{
        Name = "postgres 5433"; Url = "tcp://127.0.0.1:5433"
        Ok = $pgOk; Status = 0; Detail = $(if ($pgOk) { "accepting connections" } else { "closed" })
    }

    $flagdOk = Test-TcpPort -TargetHost "127.0.0.1" -Port 8016
    $results += [pscustomobject]@{
        Name = "flagd ofrep 8016"; Url = "tcp://127.0.0.1:8016"
        Ok = $flagdOk; Status = 0; Detail = $(if ($flagdOk) { "accepting connections" } else { "closed" })
    }

    return $results
}

function Show-HealthTable([object[]]$Results) {
    $fmt = "{0,-22} {1,-8} {2}"
    Write-Host ($fmt -f "CHECK", "STATUS", "DETAIL")
    Write-Host ($fmt -f "-----", "------", "------")
    foreach ($r in $Results) {
        $label = "OK"
        if (-not $r.Ok) { $label = "FAIL" }
        Write-Host ($fmt -f $r.Name, $label, $r.Detail)
    }
}
