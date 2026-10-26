# Start (or restart) the API detached from the calling shell, logs in runtime/.
#
#   scripts/api-detached.ps1            # start; restart if :8001 is taken by uvicorn
#   scripts/api-detached.ps1 -Stop      # stop only
#
# A detached API survives the agent harness and long scenario runs (RUNBOOK
# "Agent (P11)"). Only a process whose command line is DocOck's uvicorn is stopped.

[CmdletBinding()]
param(
    [switch]$Stop,
    [int]$Port = 8001,
    [int]$WaitSeconds = 60
)

. "$PSScriptRoot\_common.ps1"
$ErrorActionPreference = "Stop"

$root = Get-DocockRoot
$api = Join-Path $root "apps\api"
$runtime = Join-Path $root "runtime"
New-Item -ItemType Directory -Force -Path $runtime | Out-Null

$listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
foreach ($listener in $listeners) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
    if ($proc.CommandLine -notmatch "uvicorn" -or $proc.CommandLine -notmatch "app\.main:app") {
        throw "port $Port is held by a non-DocOck process: $($proc.CommandLine)"
    }
    Write-Step "stopping API pid $($proc.ProcessId)"
    Stop-Process -Id $proc.ProcessId -Force
    if ($proc.ParentProcessId) {
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($proc.ParentProcessId)"
        if ($parent -and $parent.CommandLine -match "uvicorn") {
            Stop-Process -Id $parent.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
}
if ($Stop) { return }

Start-Sleep -Seconds 1
Write-Step "starting API on 127.0.0.1:$Port"
# Win32_Process.Create starts the process outside the caller's process tree and
# inherits no handles, so neither a closing shell nor a harness that waits on
# its children holds or kills the API.
$log = Join-Path $runtime "api.log"
$err = Join-Path $runtime "api.err.log"
$command = "cmd.exe /c uv run uvicorn app.main:app --host 127.0.0.1 --port $Port > `"$log`" 2> `"$err`""
$created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create `
    -Arguments @{ CommandLine = $command; CurrentDirectory = $api }
if ($created.ReturnValue -ne 0) { throw "Win32_Process.Create failed: $($created.ReturnValue)" }

$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
    try {
        $health = Invoke-RestMethod -TimeoutSec 5 "http://127.0.0.1:$Port/api/health"
        Write-Ok "API is up ($($health.status))"
        return
    } catch {
        Start-Sleep -Seconds 2
    }
}
throw "API did not answer on :$Port within $WaitSeconds s; see runtime\api.err.log"
