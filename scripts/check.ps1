# One command for the whole quality gate: API lint/types/tests,
# then dashboard lint/tests/production build.
#
#   scripts/check.ps1
#   scripts/check.ps1 -ApiOnly

[CmdletBinding()]
param(
    [switch]$ApiOnly
)

. "$PSScriptRoot\_common.ps1"

# Native commands (uv, pnpm, next) write progress to stderr; Windows PowerShell
# turns any stderr line into a terminating error under $ErrorActionPreference =
# "Stop". Every call below is checked through $LASTEXITCODE instead.
$ErrorActionPreference = "Continue"

$root = Get-DocockRoot
$api = Join-Path $root "apps\api"
$dashboard = Join-Path $root "apps\dashboard"
$failed = @()

Push-Location $api
try {
    Write-Step "api: ruff check"
    uv run ruff check .
    if ($LASTEXITCODE -ne 0) { $failed += "ruff check" }

    Write-Step "api: ruff format --check"
    uv run ruff format --check .
    if ($LASTEXITCODE -ne 0) { $failed += "ruff format" }

    Write-Step "api: mypy"
    uv run mypy app tests
    if ($LASTEXITCODE -ne 0) { $failed += "mypy" }

    Write-Step "api: pytest"
    uv run pytest
    if ($LASTEXITCODE -ne 0) { $failed += "pytest" }
} finally {
    Pop-Location
}

if (-not $ApiOnly) {
    Push-Location $dashboard
    try {
        Write-Step "dashboard: eslint"
        pnpm lint
        if ($LASTEXITCODE -ne 0) { $failed += "eslint" }

        Write-Step "dashboard: vitest"
        pnpm test
        if ($LASTEXITCODE -ne 0) { $failed += "vitest" }

        Write-Step "dashboard: next build (type-checks)"
        pnpm build
        if ($LASTEXITCODE -ne 0) { $failed += "next build" }
    } finally {
        Pop-Location
    }
}

if ($failed.Count -gt 0) {
    Write-Fail ("failed: " + ($failed -join ", "))
    exit 1
}

Write-Ok "all checks passed"
exit 0
