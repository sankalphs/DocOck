"""Remote embeddings over the OpenAI-compatible `/embeddings` endpoint."""

from __future__ import annotations

import httpx

from app.models.embeddings.base import (
    EmbeddingError,
    EmbeddingProvider,
    EmbedKind,
    normalise,
)


class RemoteEmbeddingProvider(EmbeddingProvider):
    name = "remote"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        dimensions: int = 1024,
        timeout_s: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self.dimensions = dimensions
        self._api_key = api_key.strip()
        headers = {"User-Agent": "docock-api/0.1"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=httpx.Timeout(timeout_s, connect=min(10.0, timeout_s)),
            transport=transport,
        )

    @property
    def configured(self) -> bool:
        return bool(self._api_key) and bool(str(self._client.base_url))

    async def embed(self, texts: list[str], *, kind: EmbedKind = "document") -> list[list[float]]:
        if not texts:
            return []
        if not self.configured:
            raise EmbeddingError("unconfigured", "remote: EMBEDDING_API_KEY is not set")
        try:
            response = await self._client.post(
                "/embeddings",
                json={"model": self.model, "input": texts, "dimensions": self.dimensions},
            )
        except httpx.HTTPError as exc:
            raise EmbeddingError("upstream", f"remote: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise EmbeddingError("upstream", f"remote: HTTP {response.status_code}")
        try:
            rows = sorted(response.json()["data"], key=lambda item: item.get("index", 0))
            vectors = [normalise([float(v) for v in row["embedding"]]) for row in rows]
        except (ValueError, KeyError, TypeError) as exc:
            raise EmbeddingError("bad_response", f"remote: malformed response ({exc})") from exc
        if len(vectors) != len(texts):
            raise EmbeddingError("bad_response", "remote: vector count does not match input")
        return self.check(vectors, self.dimensions)

    async def aclose(self) -> None:
        await self._client.aclose()


__all__ = ["RemoteEmbeddingProvider"]
