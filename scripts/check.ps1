# One command for the API quality gate: lint, format check, types, tests.
#
#   scripts/check.ps1

[CmdletBinding()]
param()

. "$PSScriptRoot\_common.ps1"

$root = Get-DocockRoot
$api = Join-Path $root "apps\api"
Push-Location $api
try {
    Write-Step "ruff check"
    uv run ruff check .
    if ($LASTEXITCODE -ne 0) { throw "ruff check failed" }

    Write-Step "ruff format --check"
    uv run ruff format --check .
    if ($LASTEXITCODE -ne 0) { throw "format check failed" }

    Write-Step "mypy"
    uv run mypy app tests
    if ($LASTEXITCODE -ne 0) { throw "mypy failed" }

    Write-Step "pytest"
    uv run pytest
    if ($LASTEXITCODE -ne 0) { throw "pytest failed" }
} finally {
    Pop-Location
}

Write-Ok "all checks passed"
