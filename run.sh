#!/usr/bin/env bash
# DocOck — one command to set up, start and sanity-check the whole lab.
#
#   ./run.sh                 # first run: .env, demo checkout, Docker stack, DB, API, dashboard
#   ./run.sh --detach        # same, but leave API + dashboard running in the background
#   ./run.sh --skip-pull     # images are already present
#   ./run.sh --stop          # stop API, dashboard and both Compose projects (keeps data)
#   ./run.sh --logs          # follow runtime/api.log and runtime/dashboard.log
#   ./run.sh --check         # run the full quality gate (no Docker required)
#
# Works on macOS, Linux, WSL and Git Bash. On Windows, run it with Git Bash:
#   & "C:\Program Files\Git\bin\bash.exe" run.sh
#
# The script is intentionally idempotent: running it again stops the previous
# API/dashboard it started (PIDs live in runtime/run/) and brings the stack back
# to the same state.

set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$ROOT"

# ---------------------------------------------------------------- output ----
if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'; C_CYAN=$'\033[36m'; C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'; C_DIM=$'\033[2m'
else
  C_RESET=""; C_CYAN=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_DIM=""
fi

step() { printf '%s==> %s%s\n' "$C_CYAN" "$*" "$C_RESET"; }
ok()   { printf '%s  OK  %s%s\n' "$C_GREEN" "$*" "$C_RESET"; }
warn() { printf '%s WARN %s%s\n' "$C_YELLOW" "$*" "$C_RESET"; }
fail() { printf '%s FAIL %s%s\n' "$C_RED" "$*" "$C_RESET"; }
die()  { fail "$*"; exit 1; }
dim()  { printf '%s     %s%s\n' "$C_DIM" "$*" "$C_RESET"; }

# ---------------------------------------------------------------- options ----
SKIP_PULL=0
NO_WAIT=0
DETACH=0
SKIP_STACK=0
NO_API=0
NO_DASHBOARD=0
FRESH=0
RESEED_FLAGS=0
STOP=0
STOP_VOLUMES=0
PURGE=0
LOGS=0
CHECK=0
WAIT_SECONDS="${DOCOCK_WAIT_SECONDS:-300}"

usage() {
  cat <<'EOF'
Usage: ./run.sh [options]

Setup and run
  (none)            Bring the whole lab up and keep API + dashboard in this terminal.
  --detach          Start API + dashboard in the background, print the URLs, exit.
  --skip-pull       Do not pull Docker images (faster on repeat runs).
  --no-wait         Do not block on telemetry health checks.
  --skip-stack      Assume the Docker stack is already up; only run the apps.
  --no-api          Do not start the FastAPI control plane.
  --no-dashboard    Do not start the Next.js dashboard.
  --fresh           Delete and re-clone the pinned OpenTelemetry demo checkout.
  --reseed-flags    Copy the upstream flagd seed over runtime/flagd again.
  --wait SECONDS    Health-check timeout (default 300, or $DOCOCK_WAIT_SECONDS).

Operate
  --stop            Stop API, dashboard and both Compose projects. Keeps data.
  --volumes         With --stop: also delete the Postgres volume.
  --purge           Stop everything, delete Postgres data, and remove every Docker
                    image used by docock-demo/docock-lab. The next run re-pulls ~10 GB.
  --logs            Follow runtime/api.log and runtime/dashboard.log.
  --check           Run the quality gate: ruff, mypy, pytest, eslint, vitest, build.
  -h, --help        Show this help.

Examples
  ./run.sh                              # first run
  ./run.sh --skip-pull                  # every later run
  ./run.sh --detach                     # background, then open http://127.0.0.1:3001
  ./run.sh --stop                       # stop the apps and containers
  ./run.sh --stop --volumes             # also drop the Postgres data
  ./run.sh --purge                      # remove containers, volumes and images
  ./run.sh --check                      # verify the repo without Docker
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-pull) SKIP_PULL=1 ;;
    --no-wait) NO_WAIT=1 ;;
    --detach) DETACH=1 ;;
    --skip-stack) SKIP_STACK=1 ;;
    --no-api) NO_API=1 ;;
    --no-dashboard) NO_DASHBOARD=1 ;;
    --fresh) FRESH=1 ;;
    --reseed-flags) RESEED_FLAGS=1 ;;
    --stop) STOP=1 ;;
    --volumes) STOP_VOLUMES=1 ;;
    --purge) PURGE=1; STOP=1 ;;
    --logs) LOGS=1 ;;
    --check) CHECK=1 ;;
    --wait) shift; [[ $# -gt 0 ]] || die "--wait needs a number of seconds"; WAIT_SECONDS="$1" ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
  shift
done

# --------------------------------------------------------------- helpers ----
is_windows() {
  case "$(uname -s 2>/dev/null || echo unknown)" in
    MINGW*|MSYS*|CYGWIN*) return 0 ;;
    *) return 1 ;;
  esac
}

# Docker Desktop on Windows wants C:/... rather than /d/...; Git Bash's cygpath
# converts without changing the file that Compose reads.
host_path() {
  if is_windows && command -v cygpath >/dev/null 2>&1; then
    cygpath -m "$1"
  else
    printf '%s\n' "$1"
  fi
}

need() {
  command -v "$1" >/dev/null 2>&1 || die "missing '$1' — $2"
}

env_value() {
  local key="$1" fallback="$2" line
  line="$(grep -E "^${key}=" .env 2>/dev/null | tail -n 1 || true)"
  line="${line#*=}"
  line="${line%$'\r'}"
  line="${line%\"}"; line="${line#\"}"
  line="${line%\'}"; line="${line#\'}"
  if [[ -n "$line" ]]; then printf '%s' "$line"; else printf '%s' "$fallback"; fi
}

# A listening TCP port. nc is present on macOS/Linux; /dev/tcp covers minimal
# Bash installs. Both are cheap enough for a preflight.
tcp_open() {
  local port="$1"
  if command -v nc >/dev/null 2>&1; then
    nc -z -w 1 127.0.0.1 "$port" >/dev/null 2>&1
    return $?
  fi
  (exec 3<>"/dev/tcp/127.0.0.1/${port}") 2>/dev/null || return 1
  exec 3>&- 3<&-
  return 0
}

http_ok() { curl -fsS --max-time 6 -o /dev/null "$1" >/dev/null 2>&1; }

# No -f here: /api/health answers 503 with a useful body when a dependency is
# down, and the caller may still want to match that body.
http_match() {
  curl -sS --max-time 8 "$1" 2>/dev/null | grep -Eq "$2"
}

wait_http() {
  local name="$1" url="$2" timeout="${3:-120}" pattern="${4:-}"
  local deadline=$((SECONDS + timeout))
  while (( SECONDS < deadline )); do
    if [[ -n "$pattern" ]]; then
      http_match "$url" "$pattern" && { ok "$name ready"; return 0; }
    else
      http_ok "$url" && { ok "$name ready"; return 0; }
    fi
    sleep 2
  done
  warn "$name did not answer within ${timeout}s ($url)"
  return 1
}

# pnpm and uv both spawn a child (Next.js / uvicorn). Kill the tree so a re-run
# never leaves a half-dead server holding 8001 or 3001.
kill_tree() {
  local pid="$1" child
  for child in $(pgrep -P "$pid" 2>/dev/null || true); do
    kill_tree "$child"
  done
  kill -TERM "$pid" 2>/dev/null || true
}

stop_pidfile() {
  local file="$1" label="$2" pid
  [[ -f "$file" ]] || return 0
  pid="$(tr -d '\r\n' <"$file" 2>/dev/null || true)"
  rm -f "$file"
  [[ -n "$pid" ]] || return 0
  kill -0 "$pid" 2>/dev/null || return 0
  step "stopping previous $label (pid $pid)"
  kill_tree "$pid"
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 0.3
  done
  pkill -KILL -P "$pid" 2>/dev/null || true
  kill -KILL "$pid" 2>/dev/null || true
}

# -------------------------------------------------------------- prerun modes -
if [[ "$LOGS" -eq 1 ]]; then
  [[ -f runtime/api.log || -f runtime/dashboard.log ]] || die "no logs yet — start the lab first"
  tail -n 100 -f runtime/api.log runtime/dashboard.log
  exit 0
fi

if [[ "$CHECK" -eq 1 ]]; then
  need uv "install it from https://docs.astral.sh/uv/"
  need pnpm "install it from https://pnpm.io/installation"
  step "python dependencies"
  (cd apps/api && uv sync --frozen)
  step "node dependencies"
  pnpm install --frozen-lockfile
  step "api: ruff, format, mypy, pytest"
  (cd apps/api && uv run ruff check . ../../scripts && uv run ruff format --check . ../../scripts \
    && uv run mypy app tests && uv run pytest)
  step "dashboard: eslint, vitest, production build"
  (cd apps/dashboard && pnpm lint && pnpm test && pnpm build)
  ok "quality gate passed"
  exit 0
fi

# ------------------------------------------------------------- prerequisites -
step "checking prerequisites"
need git "install Git"
need docker "install Docker Desktop or Docker Engine"
need curl "install curl"
docker compose version >/dev/null 2>&1 || die "docker compose v2 is required (docker compose version)"
if [[ "$STOP" -eq 1 ]]; then
  if ! docker info >/dev/null 2>&1; then
    warn "Docker is not running; nothing to stop"
    exit 0
  fi
else
  docker info >/dev/null 2>&1 || die "Docker is not running — start Docker Desktop and try again"
fi
if [[ "$CHECK" -eq 0 && "$STOP" -eq 0 ]]; then
  need uv "install it from https://docs.astral.sh/uv/ (curl -LsSf https://astral.sh/uv/install.sh | sh)"
  need pnpm "install it from https://pnpm.io/installation (corepack enable pnpm)"
  need node "install Node.js 22 or newer"
  node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 22 ? 0 : 1)' \
    || die "Node.js 22+ is required (found $(node --version))"
fi
ok "prerequisites present"

API_HOST="$(env_value API_HOST 127.0.0.1)"
API_PORT="$(env_value API_PORT 8001)"
DASH_PORT="$(env_value DASHBOARD_PORT 3001)"
PG_PORT="$(env_value POSTGRES_PORT 5433)"
PG_USER="$(env_value POSTGRES_USER docock)"
PG_DB="$(env_value POSTGRES_DB docock)"
DEMO="opentelemetry-demo"
DEMO_REF="$(sed -n 's/.*"commit"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' infra/otel-demo.lock.json | head -n 1)"
DEMO_REF="${DEMO_REF:-5c8d404a85380226ff45a39dcb4159eda9552c56}"

# --------------------------------------------------------------- stop mode ---
stop_stack() {
  stop_pidfile runtime/run/api.pid "API"
  stop_pidfile runtime/run/dashboard.pid "dashboard"

  local down_flags=(--remove-orphans)
  if [[ "$STOP_VOLUMES" -eq 1 || "$PURGE" -eq 1 ]]; then
    down_flags+=(-v)
  fi
  if [[ "$PURGE" -eq 1 ]]; then
    down_flags+=(--rmi all)
  fi

  if [[ -f "$DEMO/compose.yaml" ]]; then
    step "stopping demo + observability (docock-demo)"
    local demo_args=(
      --project-directory "$(host_path "$DEMO")"
      -p docock-demo
      -f "$(host_path "$DEMO/compose.yaml")"
      -f "$(host_path "$DEMO/compose.observability.yaml")"
      -f "$(host_path "$ROOT/infra/compose/lab.override.yaml")"
    )
    DOCOCK_ROOT="$(host_path "$ROOT")" docker compose "${demo_args[@]}" down "${down_flags[@]}" \
      || warn "could not stop docock-demo (is Docker running?)"
  fi

  step "stopping lab services (docock-lab)"
  local env_file="$ROOT/.env"
  [[ -f "$env_file" ]] || env_file="$ROOT/.env.example"
  local lab_args=(--project-directory "$(host_path "$ROOT")" -p docock-lab
    --env-file "$(host_path "$env_file")" -f "$(host_path "$ROOT/infra/compose/base.yaml")" down)
  docker compose "${lab_args[@]}" "${down_flags[@]}" \
    || warn "could not stop docock-lab (is Docker running?)"

  if [[ "$PURGE" -eq 1 ]]; then
    ok "DocOck purged: containers, networks, volumes and project images removed"
  else
    ok "DocOck stopped"
  fi
}

if [[ "$STOP" -eq 1 ]]; then
  stop_stack
  exit 0
fi

# ------------------------------------------------------------------- .env ----
step "preparing .env"
if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env 2>/dev/null || true
  ok ".env created from .env.example (offline stub model by default)"
else
  ok ".env already present"
fi
mkdir -p runtime/run runtime/flagd runtime/audit

# A re-run replaces the API/dashboard this script started. Do this before the
# port preflight so our own processes never look like a conflict.
stop_pidfile runtime/run/api.pid "API"
stop_pidfile runtime/run/dashboard.pid "dashboard"

# ------------------------------------------------------------ demo checkout --
if [[ "$SKIP_STACK" -eq 0 ]]; then
  if [[ "$FRESH" -eq 1 && -d "$DEMO" ]]; then
    if docker ps -q --filter "label=com.docker.compose.project=docock-demo" 2>/dev/null | grep -q .; then
      die "the demo stack is running — run ./run.sh --stop before --fresh"
    fi
    step "removing the existing demo checkout (--fresh)"
    rm -rf "$DEMO"
  fi

  if [[ ! -f "$DEMO/compose.yaml" ]]; then
    step "cloning the OpenTelemetry demo at the pinned ref"
    git clone --depth 1 https://github.com/open-telemetry/opentelemetry-demo.git "$DEMO"
    if git -C "$DEMO" fetch --depth 1 origin "$DEMO_REF"; then
      git -C "$DEMO" checkout --detach FETCH_HEAD
    else
      warn "could not fetch $DEMO_REF; staying on the demo's default branch"
    fi
    ok "demo checkout ready"
  else
    ok "demo checkout already present"
  fi

  # flagd watches this file. A copy preserves the old mtime, and flagd ignores
  # a file whose mtime moved backwards — always touch after copying.
  if [[ "$RESEED_FLAGS" -eq 1 || ! -f runtime/flagd/demo.flagd.json ]]; then
    step "seeding runtime feature flags from upstream"
    cp "$DEMO/src/flagd/demo.flagd.json" runtime/flagd/demo.flagd.json
    touch runtime/flagd/demo.flagd.json
    ok "runtime/flagd/demo.flagd.json seeded"
  fi
fi

# ------------------------------------------------------------- port preflight -
if [[ "$SKIP_STACK" -eq 0 ]]; then
  step "checking ports"

  conflicts=()
  check_port() {
    local port="$1" label="$2" owner
    tcp_open "$port" || return 0
    owner="$(docker ps --filter "publish=${port}" --format '{{.Names}}' 2>/dev/null | head -n 1 || true)"
    [[ -n "$owner" ]] && return 0
    conflicts+=("$port ($label)")
  }
  for spec in \
    "3000:Grafana" "4000:flagd UI" "5432:other Postgres" "5433:DocOck Postgres" \
    "8000:demo frontend" "8013:flagd" "8016:flagd OFREP" "8080:frontend proxy" \
    "8089:load generator" "9090:Prometheus" "9200:OpenSearch" "14317:Jaeger OTLP" \
    "16686:Jaeger UI" "10000:frontend proxy admin"; do
    check_port "${spec%%:*}" "${spec#*:}"
  done
  # The app ports are checked separately so a previous run.sh is not a conflict.
  if [[ "$NO_API" -eq 0 ]] && tcp_open "$API_PORT"; then
    conflicts+=("$API_PORT (DocOck API from another process)")
  fi
  if [[ "$NO_DASHBOARD" -eq 0 ]] && tcp_open "$DASH_PORT"; then
    conflicts+=("$DASH_PORT (DocOck dashboard from another process)")
  fi
  if [[ ${#conflicts[@]} -gt 0 ]]; then
    fail "port conflicts:"
    for c in "${conflicts[@]}"; do dim "$c"; done
    dim "Stop the conflicting process, or run ./run.sh --stop first."
    exit 1
  fi
  ok "no conflicting ports"
fi

# --------------------------------------------------------------- compose -----
if [[ "$SKIP_STACK" -eq 0 ]]; then
  export DOCOCK_ROOT="$(host_path "$ROOT")"
  demo_compose=(
    --project-directory "$(host_path "$DEMO")"
    -p docock-demo
    -f "$(host_path "$DEMO/compose.yaml")"
    -f "$(host_path "$DEMO/compose.observability.yaml")"
    -f "$(host_path "$ROOT/infra/compose/lab.override.yaml")"
  )
  lab_compose=(
    --project-directory "$(host_path "$ROOT")"
    -p docock-lab
    --env-file "$(host_path "$ROOT/.env")"
    -f "$(host_path "$ROOT/infra/compose/base.yaml")"
  )

  if [[ "$SKIP_PULL" -eq 0 ]]; then
    step "pulling images (first run downloads several GB)"
    docker compose "${demo_compose[@]}" pull || warn "some images could not be pulled; Compose will build them"
  fi

  step "starting demo + observability (docock-demo)"
  # A cold Docker start can leave OpenSearch "unhealthy" for a minute or two.
  # Compose treats that as a hard dependency failure, so retry instead of
  # reporting the whole lab as broken.
  demo_up=0
  for attempt in 1 2 3; do
    if docker compose "${demo_compose[@]}" up -d --remove-orphans; then
      demo_up=1
      break
    fi
    if [[ "$attempt" -lt 3 ]]; then
      warn "Compose reported an unhealthy dependency; retrying in 20 s (attempt ${attempt}/3)"
      sleep 20
    fi
  done
  if [[ "$demo_up" -eq 0 ]]; then
    fail "demo stack did not become healthy"
    dim "OpenSearch is the slowest dependency on a cold start; see docs/RUNBOOK.md."
    exit 1
  fi

  step "starting lab services (docock-lab)"
  docker compose "${lab_compose[@]}" up -d
fi

# --------------------------------------------------------- telemetry health --
if [[ "$NO_WAIT" -eq 0 && "$SKIP_STACK" -eq 0 ]]; then
  step "waiting for telemetry (up to ${WAIT_SECONDS}s; first start is slow)"
  deadline=$((SECONDS + WAIT_SECONDS))
  last_report=""
  while (( SECONDS < deadline )); do
    failed=()
    http_match "http://127.0.0.1:8080/" "."                     || failed+=("demo frontend")
    http_match "http://127.0.0.1:9090/-/ready" "Ready"          || failed+=("Prometheus")
    http_match "http://127.0.0.1:9090/api/v1/query?query=count(traces_span_metrics_calls_total)" '"result":\[\{' || failed+=("span metrics")
    http_match "http://127.0.0.1:16686/jaeger/ui/api/v3/services" '"services":\[' || failed+=("Jaeger")
    http_match "http://127.0.0.1:9200/_cluster/health" '"status":"(green|yellow)"' || failed+=("OpenSearch")
    http_match "http://127.0.0.1:9200/_cat/indices/otel-logs*?format=json" "otel-logs-" || failed+=("OpenSearch logs")
    http_match "http://127.0.0.1:4000/" "."                     || failed+=("flagd UI")
    http_match "http://127.0.0.1:8089/" "."                     || failed+=("load generator")
    tcp_open "$PG_PORT"                                         || failed+=("Postgres :$PG_PORT")
    tcp_open 8016                                               || failed+=("flagd OFREP :8016")

    if [[ ${#failed[@]} -eq 0 ]]; then
      ok "all telemetry checks passed"
      break
    fi
    report="${failed[*]}"
    if [[ "$report" != "$last_report" ]]; then
      dim "waiting on: $report"
      last_report="$report"
    fi
    sleep 5
  done
  if [[ ${#failed[@]} -gt 0 ]]; then
    fail "stack is up but not healthy: ${failed[*]}"
    dim "See docs/RUNBOOK.md; then retry with ./run.sh --skip-pull --no-wait, or ./run.sh --stop."
    exit 1
  fi
fi

# ------------------------------------------------------------- python / db ---
if [[ "$NO_API" -eq 0 ]]; then
  step "installing API dependencies"
  (cd apps/api && uv sync --frozen)

  step "applying database migrations"
  for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if docker compose --project-directory "$(host_path "$ROOT")" -p docock-lab \
        --env-file "$(host_path "$ROOT/.env")" -f "$(host_path "$ROOT/infra/compose/base.yaml")" \
        exec -T postgres pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1; then
      break
    fi
    [[ "$attempt" -eq 10 ]] && die "Postgres did not become ready; check 'docker logs docock-postgres'"
    sleep 2
  done
  (cd apps/api && uv run alembic upgrade head)
  ok "database schema is current"
fi

# --------------------------------------------------------------- dashboard ---
if [[ "$NO_DASHBOARD" -eq 0 ]]; then
  step "installing dashboard dependencies"
  pnpm install --frozen-lockfile
fi

# ------------------------------------------------------------------- start ---
if [[ "$NO_API" -eq 1 && "$NO_DASHBOARD" -eq 1 ]]; then
  ok "Docker stack is running (API and dashboard skipped)."
  dim "Open http://127.0.0.1:8080 for the Astronomy Shop."
  exit 0
fi

API_PID=""
DASH_PID=""
TAIL_PID=""
STARTED=0

stop_apps() {
  [[ -n "$TAIL_PID" ]] && kill -TERM "$TAIL_PID" 2>/dev/null || true
  [[ -n "$API_PID" ]] && kill_tree "$API_PID"
  [[ -n "$DASH_PID" ]] && kill_tree "$DASH_PID"
  [[ -n "$API_PID" ]] && wait "$API_PID" 2>/dev/null || true
  [[ -n "$DASH_PID" ]] && wait "$DASH_PID" 2>/dev/null || true
  rm -f runtime/run/api.pid runtime/run/dashboard.pid
}

cleanup() {
  local code=$?
  trap - EXIT INT TERM
  if [[ "$DETACH" -eq 0 && "$STARTED" -eq 1 ]]; then
    step "stopping API and dashboard (the Docker stack keeps running)"
    stop_apps
    dim "Stop the containers too with: ./run.sh --stop"
  fi
  exit "$code"
}
trap cleanup EXIT INT TERM

start_apps() {
  if [[ "$NO_API" -eq 0 ]]; then
    step "starting API on ${API_HOST}:${API_PORT}"
    : >runtime/api.log
    (
      cd apps/api
      exec uv run uvicorn app.main:app --host "$API_HOST" --port "$API_PORT"
    ) >runtime/api.log 2>&1 &
    API_PID=$!
    echo "$API_PID" >runtime/run/api.pid
  fi

  if [[ "$NO_DASHBOARD" -eq 0 ]]; then
    step "starting dashboard on 127.0.0.1:${DASH_PORT}"
    : >runtime/dashboard.log
    (
      cd apps/dashboard
      exec pnpm exec next dev --hostname 127.0.0.1 --port "$DASH_PORT"
    ) >runtime/dashboard.log 2>&1 &
    DASH_PID=$!
    echo "$DASH_PID" >runtime/run/dashboard.pid
  fi

  STARTED=1
  if [[ "$NO_API" -eq 0 ]]; then
    if ! wait_http "API" "http://127.0.0.1:${API_PORT}/api/health" 120 '"dependencies"'; then
      fail "API did not start — last lines of runtime/api.log:"
      tail -n 30 runtime/api.log || true
      exit 1
    fi
  fi
  if [[ "$NO_DASHBOARD" -eq 0 ]]; then
    if ! wait_http "dashboard" "http://127.0.0.1:${DASH_PORT}" 180; then
      fail "dashboard did not start — last lines of runtime/dashboard.log:"
      tail -n 30 runtime/dashboard.log || true
      exit 1
    fi
  fi
}

start_apps

# ---------------------------------------------------------------- summary ----
printf '\n'
ok "DocOck is running."
printf '\n'
printf '  %-12s %s\n' "Dashboard"  "http://127.0.0.1:${DASH_PORT}"
printf '  %-12s %s\n' "Landing"    "http://127.0.0.1:${DASH_PORT}"
printf '  %-12s %s\n' "API docs"   "http://127.0.0.1:${API_PORT}/docs"
printf '  %-12s %s\n' "Astronomy"  "http://127.0.0.1:8080"
printf '  %-12s %s\n' "Prometheus" "http://127.0.0.1:9090"
printf '  %-12s %s\n' "Jaeger"     "http://127.0.0.1:16686/jaeger/ui"
printf '  %-12s %s\n' "OpenSearch" "http://127.0.0.1:9200"
printf '\n'
dim "The detector needs a 300 s warm-up before it opens an incident."
dim "Logs: runtime/api.log, runtime/dashboard.log"
dim "Stop apps: ./run.sh --stop   (add --volumes to delete Postgres data)"
printf '\n'

if [[ "$DETACH" -eq 1 ]]; then
  [[ -n "$API_PID" ]] && dim "API pid $API_PID"
  [[ -n "$DASH_PID" ]] && dim "dashboard pid $DASH_PID"
  STARTED=0
  exit 0
fi

dim "Press Ctrl-C to stop the API and dashboard. The Docker stack keeps running."
if [[ "$NO_API" -eq 0 && "$NO_DASHBOARD" -eq 0 ]]; then
  tail -n 0 -f runtime/api.log runtime/dashboard.log &
elif [[ "$NO_API" -eq 0 ]]; then
  tail -n 0 -f runtime/api.log &
else
  tail -n 0 -f runtime/dashboard.log &
fi
TAIL_PID=$!

while true; do
  if [[ -n "$API_PID" ]] && ! kill -0 "$API_PID" 2>/dev/null; then break; fi
  if [[ -n "$DASH_PID" ]] && ! kill -0 "$DASH_PID" 2>/dev/null; then break; fi
  if [[ -z "$API_PID" && -z "$DASH_PID" ]]; then break; fi
  sleep 2
done
