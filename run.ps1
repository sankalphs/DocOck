# Windows wrapper for run.sh.
#
#   .\run.ps1                 # same as ./run.sh
#   .\run.ps1 --skip-pull
#   .\run.ps1 --stop --volumes
#
# The lab runner is a Bash script. Windows PowerShell does not ship Bash, so this
# wrapper finds Git Bash (the one that comes with Git for Windows) and forwards
# every argument unchanged. WSL's bash.exe is deliberately not used: it does not
# see Docker Desktop the same way and path translation differs.

[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RunArgs
)

$ErrorActionPreference = "Stop"

$candidates = @(
    (Join-Path $env:ProgramFiles "Git\bin\bash.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Git\bin\bash.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Git\bin\bash.exe")
)

try {
    $git = (Get-Command git.exe -ErrorAction Stop).Source
    $candidates += (Join-Path (Split-Path -Parent (Split-Path -Parent $git)) "bin\bash.exe")
} catch {
    # Git is also checked by run.sh itself; just try the usual install paths.
}

$bash = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $bash) {
    Write-Host "  FAIL Git Bash not found." -ForegroundColor Red
    Write-Host "       Install Git for Windows (https://git-scm.com/download/win) or run run.sh from WSL."
    exit 1
}

$runner = Join-Path $PSScriptRoot "run.sh"
& $bash $runner @RunArgs
exit $LASTEXITCODE
