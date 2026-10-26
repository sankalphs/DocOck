"""Deterministic offline embeddings (feature hashing).

Not a semantic model: each lower-cased word and word bigram is hashed into one of
`dimensions` buckets with a signed weight, then the vector is L2-normalised. Texts
that share vocabulary are close, so retrieval tests behave sensibly without a
model download, and the same text always yields the same vector.
"""

from __future__ import annotations

import hashlib
import itertools
import re

from app.models.embeddings.base import EmbeddingProvider, EmbedKind, normalise

_WORD = re.compile(r"[a-z0-9][a-z0-9_\-]*")


def _bucket(token: str, dimensions: int) -> tuple[int, float]:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    return value % dimensions, 1.0 if (value >> 63) & 1 else -1.0


class StubEmbeddingProvider(EmbeddingProvider):
    name = "stub"

    def __init__(self, *, dimensions: int = 1024, model: str = "hashing-v1") -> None:
        self.dimensions = dimensions
        self.model = model

    def _vector(self, text: str) -> list[float]:
        words = _WORD.findall(text.lower())
        vector = [0.0] * self.dimensions
        tokens = words + [f"{a} {b}" for a, b in itertools.pairwise(words)]
        for token in tokens:
            index, sign = _bucket(token, self.dimensions)
            vector[index] += sign
        if not tokens:
            vector[0] = 1.0
        return normalise(vector)

    async def embed(self, texts: list[str], *, kind: EmbedKind = "document") -> list[list[float]]:
        return [self._vector(text) for text in texts]


__all__ = ["StubEmbeddingProvider"]
