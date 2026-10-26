"""HTTP client with timeouts and bounded retries.

Every outbound call in the API goes through this class so that:

- a slow dependency can never hold a request open indefinitely,
- transient connection resets get two quick retries instead of a 500,
- tests can assert the timeout/retry policy once instead of per adapter.
"""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.errors import DependencyError

RETRYABLE_STATUS = frozenset({429, 502, 503, 504})


class RetryableStatus(Exception):
    def __init__(self, status_code: int, url: str) -> None:
        super().__init__(f"retryable status {status_code} from {url}")
        self.status_code = status_code
        self.url = url


class AsyncHttpClient:
    def __init__(
        self,
        *,
        base_url: str = "",
        timeout_s: float = 8.0,
        retries: int = 2,
        dependency: str = "http",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.dependency = dependency
        self.retries = max(0, retries)
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_s, connect=min(timeout_s, 5.0)),
            headers={"User-Agent": "docock-api/0.1"},
            follow_redirects=True,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> AsyncHttpClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        retries: int | None = None,
    ) -> httpx.Response:
        attempts = 1 + (self.retries if retries is None else max(0, retries))
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(attempts),
                wait=wait_exponential(multiplier=0.15, max=1.0),
                retry=retry_if_exception_type((httpx.TransportError, RetryableStatus)),
                reraise=True,
            ):
                with attempt:
                    response = await self._client.request(
                        method, url, params=params, json=json, headers=headers
                    )
                    if response.status_code in RETRYABLE_STATUS:
                        raise RetryableStatus(response.status_code, str(response.url))
                    return response
        except httpx.TimeoutException as exc:
            raise DependencyError(self.dependency, f"timed out ({exc})", timeout=True) from exc
        except (httpx.HTTPError, RetryableStatus) as exc:
            raise DependencyError(self.dependency, str(exc)) from exc

        raise DependencyError(self.dependency, "request failed")  # pragma: no cover

    async def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        retries: int | None = None,
    ) -> Any:
        response = await self.request("GET", url, params=params, headers=headers, retries=retries)
        _raise_for_status(response)
        return response.json()

    async def get_text(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        retries: int | None = None,
    ) -> str:
        response = await self.request("GET", url, params=params, retries=retries)
        _raise_for_status(response)
        return response.text

    async def post_json(
        self,
        url: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        retries: int | None = None,
    ) -> Any:
        response = await self.request("POST", url, json=json, params=params, retries=retries)
        _raise_for_status(response)
        return response.json()


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code >= 400:
        body = response.text[:400]
        raise DependencyError("http", f"HTTP {response.status_code} from {response.url}: {body}")


def ensure_ok(response: httpx.Response) -> None:
    """Public status check for adapters that need to inspect a specific code first."""
    _raise_for_status(response)


_shared: dict[str, AsyncHttpClient] = {}


def shared_client(
    name: str,
    *,
    base_url: str,
    timeout_s: float = 8.0,
    retries: int = 2,
) -> AsyncHttpClient:
    """One keep-alive client per dependency.

    Docker Desktop's loopback port proxy costs ~0.5 s per *new* TCP connection,
    so reconnect-per-request turns a 4-endpoint health check into a 2 s request.
    Keeping the pool warm is what makes polling cheap.
    """
    client = _shared.get(name)
    if client is None:
        client = AsyncHttpClient(
            base_url=base_url, timeout_s=timeout_s, retries=retries, dependency=name
        )
        _shared[name] = client
    return client


async def close_shared_clients() -> None:
    for client in list(_shared.values()):
        await client.aclose()
    _shared.clear()
