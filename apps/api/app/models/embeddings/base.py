"""The `EmbeddingProvider` contract.

Memory (P12) stores `vector(1024)`; a provider that returns any other width is a
configuration error, not something to pad or truncate silently. `check()` is
the guard every caller uses before writing a vector.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import Literal

EmbedKind = Literal["document", "query"]


class EmbeddingError(Exception):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


class EmbeddingProvider(ABC):
    name: str
    model: str
    dimensions: int

    @property
    def configured(self) -> bool:
        return True

    @property
    def loaded(self) -> bool:
        return True

    @abstractmethod
    async def embed(self, texts: list[str], *, kind: EmbedKind = "document") -> list[list[float]]:
        """One L2-normalised vector per text, in input order."""

    async def embed_one(self, text: str, *, kind: EmbedKind = "document") -> list[float]:
        return (await self.embed([text], kind=kind))[0]

    def check(self, vectors: list[list[float]], expected: int) -> list[list[float]]:
        for vector in vectors:
            if len(vector) != expected:
                raise EmbeddingError(
                    "dimension_mismatch",
                    f"{self.name}:{self.model} returned {len(vector)}-d vectors, "
                    f"the store expects {expected}-d",
                )
            if not all(math.isfinite(value) for value in vector):
                raise EmbeddingError("bad_vector", f"{self.name} returned a non-finite value")
        return vectors

    async def aclose(self) -> None:  # noqa: B027 - optional hook
        """Release resources."""


def normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


__all__ = ["EmbedKind", "EmbeddingError", "EmbeddingProvider", "normalise"]
