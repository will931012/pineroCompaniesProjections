"""Embedding port. Search uses whichever provider is configured, or none.

The default provider runs an open model locally through ONNX Runtime (fastembed), so no
API key is needed and passages never leave the server. Every stored vector records the
model that produced it, and only vectors from the active model are searched.
"""

import logging
import threading
from collections.abc import Sequence
from functools import lru_cache
from typing import Protocol

from app.core.config import Settings, get_settings
from app.db.models.filings import EMBEDDING_DIMENSIONS

logger = logging.getLogger(__name__)


class EmbeddingProvider(Protocol):
    name: str
    dimensions: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedProvider:
    """Local ONNX embeddings. The model is downloaded once to `cache_dir`, then loaded lazily."""

    def __init__(self, model_name: str, cache_dir: str | None, dimensions: int) -> None:
        self.name = model_name
        self.dimensions = dimensions
        self._cache_dir = cache_dir
        self._model: object | None = None
        self._lock = threading.Lock()

    def _load(self):  # type: ignore[no-untyped-def]
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding

                self._model = TextEmbedding(self.name, cache_dir=self._cache_dir)
            return self._model

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        model = self._load()
        return [vector.tolist() for vector in model.embed(list(texts), batch_size=32)]

    def embed_query(self, text: str) -> list[float]:
        model = self._load()
        return next(iter(model.query_embed(text))).tolist()


@lru_cache
def _provider(name: str, model: str, cache_dir: str | None) -> EmbeddingProvider | None:
    if name == "none":
        return None
    return FastEmbedProvider(model, cache_dir, EMBEDDING_DIMENSIONS)


def get_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider | None:
    settings = settings or get_settings()
    return _provider(
        settings.embedding_provider, settings.embedding_model, settings.embedding_cache_dir
    )
