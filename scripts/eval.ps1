# One command for the M4 evaluation: the full scenario suite (detect, analyse,
# investigate, approve, execute, verify, report) and the secret masking audit,
# then a scoreboard. Needs the stack (scripts/up.ps1) and the API
# (scripts/api-detached.ps1) up.
#
#   scripts/eval.ps1                    # S1-S8 + W1 with remediation (~2 h)
#   scripts/eval.ps1 -Only S1,S5,W1     # a subset
#   scripts/eval.ps1 -AnalysisOnly      # detection + analysis + agent, no remediation
#
# Writes runtime/eval.json, runtime/scoreboard.md and runtime/secret-audit.json.
# Exit code 0 only when the scenario gate and the audit both pass. The stack is
# left clean: no fault active, no incident open.

[CmdletBinding()]
param(
    [string]$Only = "",
    [switch]$AnalysisOnly,
    [string]$OutDir = "runtime"
)

. "$PSScriptRoot\_common.ps1"
$ErrorActionPreference = "Continue"

$root = Get-DocockRoot
$project = Join-Path $root "apps\api"
$out = Join-Path $root $OutDir
New-Item -ItemType Directory -Force $out | Out-Null
$failed = @()

Push-Location $root
try {
    Write-Step "api reachable"
    try {
        $health = Invoke-RestMethod -TimeoutSec 10 "http://127.0.0.1:8001/api/health"
        Write-Ok "api health: $($health.status)"
    } catch {
        Write-Fail "API not reachable on :8001 - start it with scripts/api-detached.ps1"
        exit 1
    }

    Write-Step "scenario suite"
    $runner = @("run", "--project", $project, "python", "-u", "scripts\run-scenarios.py",
        "--out", (Join-Path $out "eval.json"),
        "--scoreboard", (Join-Path $out "scoreboard.md"))
    if ($AnalysisOnly) { $runner += "--agent" } else { $runner += "--remediate" }
    if ($Only) { $runner += @("--only", $Only) }
    & uv @runner
    if ($LASTEXITCODE -ne 0) { $failed += "scenario gate" }

    Write-Step "secret masking audit"
    & uv run --project $project python scripts\secret-audit.py --out (Join-Path $out "secret-audit.json")
    if ($LASTEXITCODE -ne 0) { $failed += "secret audit" }
}
finally {
    Pop-Location
}

if ($failed.Count -gt 0) {
    Write-Fail ("failed: " + ($failed -join ", "))
    exit 1
}
Write-Ok "evaluation passed - scoreboard: $(Join-Path $out 'scoreboard.md')"
exit 0
