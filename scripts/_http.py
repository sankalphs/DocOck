"""HTTP client for the live check scripts.

The API rate-limits writes (apps/api/app/ratelimit.py), for example ten fault
injections a minute. A check that walks the whole fault catalogue goes over
that on purpose, so this client waits for `Retry-After` and sends the request
again instead of recording a 429 as a failed check. A refused request does not
count against the window, so the retry gets through.
"""

from __future__ import annotations

import time

import httpx

MAX_WAITS = 6


class RateLimitedTransport(httpx.HTTPTransport):
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        for _ in range(MAX_WAITS):
            response = super().handle_request(request)
            if response.status_code != 429:
                return response
            wait = float(response.headers.get("Retry-After") or 5)
            response.close()
            print(f"  ... rate limited on {request.url.path}, waiting {wait:.0f} s", flush=True)
            time.sleep(wait + 0.5)
        return super().handle_request(request)


def client(timeout: float, retries: int = 0) -> httpx.Client:
    return httpx.Client(timeout=timeout, transport=RateLimitedTransport(retries=retries))
