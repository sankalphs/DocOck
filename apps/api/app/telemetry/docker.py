"""Docker CLI access.

We shell out to the Docker CLI instead of binding the socket: the API runs on the
host, the CLI is already the supported entry point on Windows, and it keeps the
process free of a Docker SDK dependency. Every call is bounded by a timeout so a
wedged daemon cannot block the event loop.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any


class DockerError(RuntimeError):
    pass


async def run_docker(*args: str, timeout_s: float = 10.0) -> str:
    """Run a docker command and return stdout. Raises DockerError on failure."""
    process = await asyncio.create_subprocess_exec(
        "docker",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise DockerError(f"docker {args[0]} timed out after {timeout_s}s") from exc

    if process.returncode != 0:
        message = stderr.decode("utf-8", "replace").strip() or "no stderr"
        raise DockerError(f"docker {args[0]} exited {process.returncode}: {message}")
    return stdout.decode("utf-8", "replace")


async def docker_json(*args: str, timeout_s: float = 15.0) -> Any:
    """Run a `--format {{json ...}}` command and parse each line as JSON."""
    output = (await run_docker(*args, timeout_s=timeout_s)).strip()
    if not output:
        return []
    return [json.loads(line) for line in output.splitlines() if line.strip()]


async def server_version() -> str:
    return (await run_docker("version", "--format", "{{.Server.Version}}", timeout_s=8.0)).strip()
