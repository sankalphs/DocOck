"""Secret masking audit (P16): no key, password or approval token leaks.

    uv run --project apps/api python scripts/secret-audit.py --out runtime/secret-audit.json

Scans, without ever printing a secret:

1. every GET endpoint in the API's OpenAPI document (path parameters filled
   with a real incident, approval and agent run), plus the Markdown report;
2. the dashboard's HTML for every section;
3. runtime logs and the remediation audit file;
4. every git-tracked file (what would be committed or pushed).

It looks for (a) the literal values of secret-like variables in `.env`
(`*_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`, the DSN password) and (b) generic
shapes: provider keys, bearer tokens, a DSN with a password, an approval token
or its SHA-256 in JSON. A finding is reported with its location, the rule and a
redacted fingerprint (length + first two characters). Exit code 1 on any finding.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
API = "http://127.0.0.1:8001"
DASHBOARD = "http://127.0.0.1:3001"
SECRET_KEY = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD)$")
# Values shorter than this are not scanned literally: a short local password such
# as the lab's Postgres default equals the database and user name, which appear
# legitimately everywhere. They are counted in the report instead.
MIN_LITERAL = 12
PATTERNS = {
    "provider_key": re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}"),
    "bearer_token": re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}=*"),
    "dsn_password": re.compile(r"\b[a-z][a-z0-9+.-]*://[^:/\s\"'@]+:[^@\s\"'/]{3,}@"),
    "approval_token_json": re.compile(r"\"token\"\s*:\s*\"[A-Za-z0-9_-]{30,}\""),
    "token_hash_json": re.compile(r"\"token_hash\"\s*:\s*\"[0-9a-f]{64}\""),
}
# Matches that are not secrets are listed under "allowed" with the reason, never
# dropped silently: a DSN password equal to the lab default that .env.example
# publishes, and test fixtures whose key body says it is fake.
FAKE_KEY = re.compile(r"^sk-(?:ant-|proj-)?(?:test|fake|dummy|example)", re.IGNORECASE)
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".lock", ".pdf"}


def load_env_secrets() -> tuple[dict[str, str], list[str]]:
    secrets: dict[str, str] = {}
    short: list[str] = []
    env = ROOT / ".env"
    if not env.exists():
        return secrets, short
    for line in env.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("'\"")
        if key.endswith("_URL") and "@" in value:
            password = urlsplit(value).password
            if password:
                key, value = f"{key}(password)", password
            else:
                continue
        elif not SECRET_KEY.search(key):
            continue
        if not value:
            continue
        if len(value) < MIN_LITERAL:
            short.append(key)
        else:
            secrets[key] = value
    return secrets, short


def public_dsn_passwords() -> set[str]:
    """DSN passwords that .env.example publishes as the local lab default."""
    example = ROOT / ".env.example"
    if not example.exists():
        return set()
    found = set()
    for line in example.read_text(encoding="utf-8").splitlines():
        _, _, value = line.partition("=")
        if "://" in value and "@" in value:
            password = urlsplit(value.strip()).password
            if password:
                found.add(password)
    return found


def fingerprint(value: str) -> str:
    return f"len={len(value)} starts={value[:2]}..."


class Audit:
    def __init__(self, secrets: dict[str, str], public: set[str]) -> None:
        self.secrets = secrets
        self.public = public
        self.findings: list[dict[str, Any]] = []
        self.allowed: list[dict[str, Any]] = []
        self.scanned: dict[str, int] = {"api": 0, "dashboard": 0, "logs": 0, "tracked": 0}

    def scan(self, kind: str, where: str, text: str) -> None:
        self.scanned[kind] += 1
        for name, value in self.secrets.items():
            if value in text:
                self.findings.append(
                    {"where": where, "rule": f"env:{name}", "match": fingerprint(value)}
                )
        for rule, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                found = match.group(0)
                if rule == "dsn_password" and re.search(
                    r"://[^:]+:(\*+|<[^>]+>|\$\{?\w+\}?)@", found
                ):
                    continue  # masked or a placeholder
                item = {"where": where, "rule": rule, "match": fingerprint(found)}
                reason = self.why_allowed(rule, found)
                if reason:
                    self.allowed.append({**item, "reason": reason})
                else:
                    self.findings.append(item)

    def why_allowed(self, rule: str, found: str) -> str | None:
        if rule == "dsn_password":
            password = found.rsplit(":", 1)[1].rstrip("@")
            if password in self.public:
                return "the local lab default published in .env.example"
        if rule == "provider_key" and FAKE_KEY.match(found):
            return "a test fixture marked fake in the key itself"
        return None


def api_paths(client: httpx.Client) -> list[str]:
    spec = client.get(f"{API}/openapi.json").json()
    incident = client.get(f"{API}/api/incidents?limit=1").json()["items"][0]["public_id"]
    approvals = client.get(f"{API}/api/approvals?limit=1").json()["items"]
    runs = client.get(f"{API}/api/agent/runs?limit=1").json()
    run_items = runs.get("items", runs) if isinstance(runs, dict) else runs
    values = {
        "ref": incident,
        "approval_id": str(approvals[0]["id"]) if approvals else "1",
        "run_id": str(run_items[0]["id"]) if run_items else "1",
    }
    paths: list[str] = []
    for path, ops in spec["paths"].items():
        if "get" not in ops or path == "/api/events":
            continue
        params = re.findall(r"{(\w+)}", path)
        if any(param not in values for param in params):
            continue
        for param in params:
            path = path.replace("{" + param + "}", values[param])
        paths.append(path)
    paths.append(f"/api/incidents/{incident}/report.md")
    return sorted(set(paths))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="runtime/secret-audit.json")
    args = parser.parse_args()
    secrets, short = load_env_secrets()
    audit = Audit(secrets, public_dsn_passwords())
    errors: list[str] = []
    with httpx.Client(timeout=60.0) as client:
        for api_path in api_paths(client):
            response = client.get(f"{API}{api_path}")
            audit.scan("api", api_path, response.text)
            if response.status_code >= 500:
                errors.append(f"{api_path} {response.status_code}")
        for section in (
            "/",
            "/faults",
            "/telemetry",
            "/incidents",
            "/agent",
            "/approvals",
            "/reports",
            "/memory",
            "/models",
            "/settings",
        ):
            try:
                response = client.get(f"{DASHBOARD}{section}")
                audit.scan("dashboard", section, response.text)
            except httpx.HTTPError as exc:
                errors.append(f"dashboard {section}: {exc}")
    runtime = ROOT / "runtime"
    for path in [*runtime.glob("*.log"), *runtime.glob("audit/*.jsonl")]:
        audit.scan(
            "logs", str(path.relative_to(ROOT)), path.read_text(encoding="utf-8", errors="replace")
        )
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    for name in tracked:
        path = ROOT / name
        if path.suffix.lower() in SKIP_SUFFIXES or not path.is_file():
            continue
        audit.scan("tracked", name, path.read_text(encoding="utf-8", errors="replace"))

    result = {
        "generated_at": datetime.now(UTC).isoformat(),
        "env_secrets_scanned": sorted(secrets),
        "env_secrets_too_short_to_scan": sorted(short),
        "scanned": audit.scanned,
        "findings": audit.findings,
        "allowed": audit.allowed,
        "errors": errors,
    }
    Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"scanned: {audit.scanned}")
    print(
        f"env secrets scanned literally: {len(secrets)} "
        f"({', '.join(sorted(secrets)) or 'none set'})"
    )
    if short:
        print(f"too short to scan literally: {', '.join(sorted(short))}")
    for item in audit.allowed:
        print(f"  allowed {item['rule']} in {item['where']}: {item['reason']}")
    for finding in audit.findings:
        print(f"  FINDING {finding['rule']} in {finding['where']} ({finding['match']})")
    for error in errors:
        print(f"  error: {error}")
    print(f"{len(audit.findings)} finding(s) -> {args.out}")
    return 1 if audit.findings or errors else 0


if __name__ == "__main__":
    sys.exit(main())
