"""Qwen3-Embedding-0.6B on CPU through ONNX Runtime (int8).

Facts verified on this host (2026-10-26) against
`onnx-community/Qwen3-Embedding-0.6B-ONNX`, `onnx/model_int8.onnx` (614 MB):

- the export is a decoder with a KV cache: besides `input_ids`, `attention_mask`
  and `position_ids` it requires 28 x `past_key_values.N.{key,value}` inputs; an
  empty cache (`past_sequence_length = 0`) is a plain forward pass;
- `tokenizer.json` already appends `<|endoftext|>`, which is the token Qwen3's
  last-token pooling reads, so nothing is appended here;
- output `last_hidden_state` is `[batch, seq, 1024]`; the embedding is the hidden
  state of the last *real* token (right padding + gather), L2-normalised;
- queries carry Qwen3's instruction prefix, documents do not.

Loading takes ~2 s; a batch of three short texts ~0.15 s. The model files are
downloaded once into `runtime/models/` (gitignored), never into the repo.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.logging import get_logger
from app.models.embeddings.base import EmbeddingError, EmbeddingProvider, EmbedKind

log = get_logger(__name__)

DEFAULT_REPO = "onnx-community/Qwen3-Embedding-0.6B-ONNX"
DEFAULT_FILE = "onnx/model_int8.onnx"
QUERY_INSTRUCTION = "Given an incident description, retrieve similar resolved incidents"
SUPPORT_FILES = ("config.json", "tokenizer.json")


class OnnxLocalEmbeddingProvider(EmbeddingProvider):
    name = "onnx_local"

    def __init__(
        self,
        *,
        model: str,
        dimensions: int = 1024,
        repo: str = DEFAULT_REPO,
        onnx_file: str = DEFAULT_FILE,
        cache_dir: Path,
        allow_download: bool = True,
        max_tokens: int = 512,
        batch_size: int = 8,
        threads: int = 4,
        query_instruction: str = QUERY_INSTRUCTION,
    ) -> None:
        self.model = model
        self.dimensions = dimensions
        self.repo = repo
        self.onnx_file = onnx_file
        self.cache_dir = cache_dir
        self.allow_download = allow_download
        self.max_tokens = max_tokens
        self.batch_size = max(1, batch_size)
        self.threads = max(1, threads)
        self.query_instruction = query_instruction
        self._session: Any = None
        self._tokenizer: Any = None
        self._kv_inputs: list[tuple[str, int, int]] = []
        self._lock = asyncio.Lock()

    @property
    def loaded(self) -> bool:
        return self._session is not None

    def _resolve(self, filename: str) -> str:
        from huggingface_hub import hf_hub_download

        try:
            return str(
                hf_hub_download(
                    self.repo,
                    filename,
                    cache_dir=str(self.cache_dir),
                    local_files_only=not self.allow_download,
                )
            )
        except Exception as exc:  # network, missing cache, bad repo
            raise EmbeddingError(
                "model_unavailable",
                f"onnx_local: cannot load {self.repo}/{filename} "
                f"({type(exc).__name__}); set EMBEDDING_ALLOW_DOWNLOAD=true or use the stub",
            ) from exc

    def _load_sync(self) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        for name in SUPPORT_FILES:
            self._resolve(name)
        tokenizer = Tokenizer.from_file(self._resolve("tokenizer.json"))
        tokenizer.no_padding()
        tokenizer.enable_truncation(self.max_tokens)
        options = ort.SessionOptions()
        options.intra_op_num_threads = self.threads
        session = ort.InferenceSession(
            self._resolve(self.onnx_file), options, providers=["CPUExecutionProvider"]
        )
        kv: list[tuple[str, int, int]] = []
        for item in session.get_inputs():
            if item.name.startswith("past_key_values"):
                heads, head_dim = int(item.shape[1]), int(item.shape[3])
                kv.append((item.name, heads, head_dim))
        output = next(o for o in session.get_outputs() if o.name == "last_hidden_state")
        width = output.shape[-1]
        if isinstance(width, int) and width != self.dimensions:
            raise EmbeddingError(
                "dimension_mismatch",
                f"onnx_local: {self.onnx_file} produces {width}-d, configured {self.dimensions}-d",
            )
        self._tokenizer = tokenizer
        self._kv_inputs = kv
        self._session = session

    async def load(self) -> None:
        async with self._lock:
            if self._session is None:
                await asyncio.to_thread(self._load_sync)
                log.info("embedding_model_loaded", repo=self.repo, file=self.onnx_file)

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        import numpy as np

        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            encodings = [self._tokenizer.encode(text).ids for text in batch]
            length = max(len(ids) for ids in encodings)
            size = len(encodings)
            input_ids = np.zeros((size, length), dtype=np.int64)
            mask = np.zeros((size, length), dtype=np.int64)
            for row, ids in enumerate(encodings):
                input_ids[row, : len(ids)] = ids
                mask[row, : len(ids)] = 1
            feed: dict[str, Any] = {
                "input_ids": input_ids,
                "attention_mask": mask,
                "position_ids": np.tile(np.arange(length, dtype=np.int64), (size, 1)),
            }
            for name, heads, head_dim in self._kv_inputs:
                feed[name] = np.zeros((size, heads, 0, head_dim), dtype=np.float32)
            hidden = self._session.run(["last_hidden_state"], feed)[0]
            pooled = np.stack([hidden[row, len(ids) - 1] for row, ids in enumerate(encodings)])
            pooled = pooled / np.linalg.norm(pooled, axis=1, keepdims=True)
            vectors.extend(pooled.astype(float).tolist())
        return vectors

    async def embed(self, texts: list[str], *, kind: EmbedKind = "document") -> list[list[float]]:
        if not texts:
            return []
        await self.load()
        prepared = [
            f"Instruct: {self.query_instruction}\nQuery:{text}" if kind == "query" else text
            for text in texts
        ]
        # ONNX Runtime is thread-safe, but serialising keeps CPU use bounded
        # next to the detector loop.
        async with self._lock:
            vectors = await asyncio.to_thread(self._embed_sync, prepared)
        return self.check(vectors, self.dimensions)

    async def aclose(self) -> None:
        self._session = None
        self._tokenizer = None


__all__ = ["DEFAULT_FILE", "DEFAULT_REPO", "QUERY_INSTRUCTION", "OnnxLocalEmbeddingProvider"]
