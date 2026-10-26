# Starts the full local lab: demo + observability + Postgres/pgvector.
#
#   scripts/up.ps1
#   scripts/up.ps1 -SkipPull          # images already present
#   scripts/up.ps1 -NoWait            # do not block on health checks
#   scripts/up.ps1 -ReseedFlags       # reset runtime flags from upstream

[CmdletBinding()]
param(
    [switch]$SkipPull,
    [switch]$NoWait,
    [switch]$ReseedFlags,
    [int]$WaitSeconds = 300
)

. "$PSScriptRoot\_common.ps1"

$root = Get-DocockRoot
Set-Location $root

# ---------------------------------------------------------------- config ----
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $root ".env.example") $envFile
    Write-Ok ".env created from .env.example (fill in LLM_API_KEY later)"
}

$demo = Join-Path $root "opentelemetry-demo"
if (-not (Test-Path (Join-Path $demo "compose.yaml"))) {
    Write-Step "demo checkout missing; bootstrapping"
    & "$PSScriptRoot\bootstrap-demo.ps1"
    if ($LASTEXITCODE -ne 0) { throw "demo bootstrap failed" }
}

# --------------------------------------------------------- runtime flags ----
$runtimeFlagd = Join-Path $root "runtime\flagd"
$targetFlagFile = Join-Path $runtimeFlagd "demo.flagd.json"
$seedFlagFile = Join-Path $demo "src\flagd\demo.flagd.json"

New-Item -ItemType Directory -Force $runtimeFlagd | Out-Null
$needsSeed = $ReseedFlags -or (-not (Test-Path $targetFlagFile))
if (-not $needsSeed) {
    try {
        Get-Content $targetFlagFile -Raw | ConvertFrom-Json | Out-Null
    } catch {
        Write-Warn "runtime flag file is not valid JSON; reseeding"
        $needsSeed = $true
    }
}
if ($needsSeed) {
    Copy-FlagSeed -Source $seedFlagFile -Destination $targetFlagFile
    Write-Ok "runtime flags seeded from upstream"
}

# ------------------------------------------------------------- preflight ----
Write-Step "checking ports"
$ports = @(3000, 4000, 5432, 5433, 8000, 8013, 8016, 8080, 8089, 9090, 9200, 14317, 16686, 10000)
$conflicts = @()
foreach ($port in $ports) {
    if (Test-TcpPort -TargetHost "127.0.0.1" -Port $port -TimeoutMs 300) {
        $owner = (docker ps --filter "publish=$port" --format "{{.Names}}" 2>$null | Select-Object -First 1)
        if ([string]::IsNullOrWhiteSpace($owner)) {
            $conflicts += "$port (owned by another process)"
        }
    }
}
if ($conflicts.Count -gt 0) {
    Write-Fail "port conflicts:"
    $conflicts | ForEach-Object { Write-Host "       $_" }
    Write-Host "     Stop the conflicting process, or run scripts/down.ps1 first."
    exit 1
}
Write-Ok "no conflicting ports"

# --------------------------------------------------------------- compose ----
$env:DOCOCK_ROOT = $root

$demoCompose = @(
    "--project-directory", $demo,
    "-p", "docock-demo",
    "-f", (Join-Path $demo "compose.yaml"),
    "-f", (Join-Path $demo "compose.observability.yaml"),
    "-f", (Join-Path $root "infra\compose\lab.override.yaml")
)

if (-not $SkipPull) {
    Write-Step "pulling images (first run is several GB)"
    docker compose @demoCompose pull
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "some images could not be pulled; compose will build the missing ones"
    }
}

Write-Step "starting demo + observability (docock-demo)"
docker compose @demoCompose up -d --remove-orphans
if ($LASTEXITCODE -ne 0) { throw "demo stack failed to start" }

Write-Step "starting lab services (docock-lab)"
docker compose -p docock-lab --env-file $envFile -f (Join-Path $root "infra\compose\base.yaml") up -d
if ($LASTEXITCODE -ne 0) { throw "lab services failed to start" }

Write-Host ""
Write-Host "  frontend   http://127.0.0.1:8080" -ForegroundColor Gray
Write-Host "  prometheus http://127.0.0.1:9090" -ForegroundColor Gray
Write-Host "  jaeger     http://127.0.0.1:16686/jaeger/ui" -ForegroundColor Gray
Write-Host "  opensearch http://127.0.0.1:9200" -ForegroundColor Gray
Write-Host "  flagd ui   http://127.0.0.1:4000" -ForegroundColor Gray
Write-Host ""

if (-not $NoWait) {
    Write-Step "waiting for health (up to ${WaitSeconds}s)"
    & "$PSScriptRoot\health.ps1" -WaitSeconds $WaitSeconds
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "stack is up but unhealthy; see docs/RUNBOOK.md"
        exit 1
    }
}

Write-Ok "stack is running"
