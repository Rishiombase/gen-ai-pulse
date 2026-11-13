"""Optional local sentence embeddings via fastembed (ONNX, CPU, free, no API key).

If fastembed is not installed or the model cannot be downloaded, `Embedder.available`
is False and the pipeline falls back to lexicon-only classification and dedupe.
"""
from __future__ import annotations

import logging
import os

from . import config

log = logging.getLogger(__name__)

MODEL_NAME = os.environ.get("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
CACHE_DIR = os.environ.get("FASTEMBED_CACHE_PATH", str(config.ROOT / ".cache" / "fastembed"))


class Embedder:
    def __init__(self, enabled: bool = True) -> None:
        self.available = False
        self._model = None
        if not enabled:
            return
        try:
            import numpy as np  # noqa: F401
            from fastembed import TextEmbedding
            self._model = TextEmbedding(MODEL_NAME, cache_dir=CACHE_DIR)
            self.available = True
            log.info("embeddings: using %s", MODEL_NAME)
        except Exception as exc:  # ImportError, network failure, etc.
            log.warning("embeddings unavailable (%s); using lexicon-only mode", exc)

    def embed(self, texts: list[str]):
        """Return an (n, d) array of L2-normalised vectors."""
        import numpy as np
        if not texts:
            return np.zeros((0, 384), dtype="float32")
        vecs = np.array(list(self._model.embed(texts, batch_size=64)), dtype="float32")
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / np.clip(norms, 1e-9, None)
