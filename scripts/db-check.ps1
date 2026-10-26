# Proves the Alembic baseline applies to an EMPTY database, not just to the one
# that already had tables. Creates a scratch database, migrates up, downgrades,
# and drops it. The real `docock` database is never touched.
#
#   scripts/db-check.ps1

[CmdletBinding()]
param()

. "$PSScriptRoot\_common.ps1"

# Native commands write progress and NOTICE lines to stderr, and Windows
# PowerShell turns any stderr line into a terminating error once
# $ErrorActionPreference is "Stop". Every native call below is checked through
# $LASTEXITCODE instead, so relax it for the duration of this script.
$ErrorActionPreference = "Continue"

$root = Get-DocockRoot
$api = Join-Path $root "apps\api"
$scratch = "docock_migrate_check"
$container = "docock-postgres"
$expectedTables = 16  # 15 domain tables + alembic_version

function Invoke-Psql([string]$Database, [string]$Sql) {
    $output = docker exec $container psql -U docock -d $Database -tA -v ON_ERROR_STOP=1 -c $Sql 2>$null
    if ($LASTEXITCODE -ne 0) { throw "psql failed on $Database : $Sql" }
    return ($output | Out-String).Trim()
}

Write-Step "checking postgres container"
$running = docker ps --filter "name=$container" --format "{{.Names}}"
if ($running -notmatch $container) { throw "$container is not running; run scripts\up.ps1 first" }
Write-Ok "postgres reachable"

Write-Step "creating scratch database $scratch"
Invoke-Psql "postgres" "DROP DATABASE IF EXISTS $scratch" | Out-Null
Invoke-Psql "postgres" "CREATE DATABASE $scratch" | Out-Null

$previous = $env:DATABASE_URL
$env:DATABASE_URL = "postgresql+asyncpg://docock:docock@127.0.0.1:5433/$scratch"

Push-Location $api
try {
    Write-Step "alembic upgrade head on empty database"
    $upgrade = & uv run alembic upgrade head 2>&1
    if ($LASTEXITCODE -ne 0) {
        $upgrade | Out-Host
        throw "alembic upgrade failed"
    }

    $tables = [int](Invoke-Psql $scratch "select count(*) from information_schema.tables where table_schema='public'")
    if ($tables -ne $expectedTables) {
        throw "expected $expectedTables tables after upgrade, found $tables"
    }
    Write-Ok "$tables tables created"

    $version = Invoke-Psql $scratch "select version_num from alembic_version"
    Write-Ok "alembic_version = $version"

    Write-Step "alembic downgrade base"
    $downgrade = & uv run alembic downgrade base 2>&1
    if ($LASTEXITCODE -ne 0) {
        $downgrade | Out-Host
        throw "alembic downgrade failed"
    }

    $remaining = [int](Invoke-Psql $scratch "select count(*) from information_schema.tables where table_schema='public'")
    if ($remaining -gt 1) { throw "expected only alembic_version after downgrade, found $remaining tables" }
    Write-Ok "downgrade is clean ($remaining table left: alembic_version)"
} finally {
    Pop-Location
    if ($null -eq $previous) {
        Remove-Item Env:\DATABASE_URL -ErrorAction SilentlyContinue
    } else {
        $env:DATABASE_URL = $previous
    }
    Write-Step "dropping scratch database"
    Invoke-Psql "postgres" "DROP DATABASE IF EXISTS $scratch" | Out-Null
}

Write-Ok "migrations apply to an empty database and roll back cleanly"
