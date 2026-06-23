"""
Vault Embedder
==============
Generates text embeddings for vaulted parts using sentence-transformers.

Each part gets an embedding computed from:
  "{name} {category} {specs_summary} {status}"

This embedding is stored as a BLOB in SQLite and used for
semantic search ("find me a 5V motor driver" → cosine similarity).

Uses: all-MiniLM-L6-v2 (384-dim, same model as RAG fallback)
"""

from __future__ import annotations

import json
from typing import Optional

import numpy as np

try:
    from sentence_transformers import SentenceTransformer
    SBERT_AVAILABLE = True
except ImportError:
    SBERT_AVAILABLE = False


class VaultEmbedder:
    """
    Generate embeddings for parts stored in the vault.

    Usage:
        embedder = VaultEmbedder()
        embedding = embedder.embed_part(name="ESP32", category="microcontroller",
                                         specs={"voltage": "3.3V"}, status="functional")
        # embedding is a numpy float32 array (384,)
        blob = embedder.to_blob(embedding)   # for SQLite storage
        array = embedder.from_blob(blob)     # to restore
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        if not SBERT_AVAILABLE:
            print("[Embedder] WARNING: sentence-transformers not available")
            self.model = None
            self.dim = 384
            return

        self.model = SentenceTransformer(model_name)
        # Handle both old and new sentence-transformers API
        if hasattr(self.model, 'get_sentence_embedding_dimension'):
            self.dim = self.model.get_sentence_embedding_dimension()
        elif hasattr(self.model, 'get_embedding_dimension'):
            self.dim = self.model.get_embedding_dimension()
        else:
            self.dim = 384  # default for all-MiniLM-L6-v2
        print(f"[Embedder] Model loaded: {model_name} (dim={self.dim})")

    def _build_text(
        self,
        name: str,
        category: str = "",
        specs: Optional[dict] = None,
        status: str = "",
    ) -> str:
        """Build a rich text representation of a part for embedding."""
        parts = [name]
        if category:
            parts.append(category)
        if specs:
            # Flatten specs into key=value pairs
            for k, v in specs.items():
                if v and str(v).strip():
                    parts.append(f"{k}: {v}")
        if status:
            parts.append(f"condition: {status}")
        return " ".join(parts)

    def embed_part(
        self,
        name: str,
        category: str = "",
        specs: Optional[dict] = None,
        status: str = "",
    ) -> Optional[np.ndarray]:
        """
        Generate embedding for a single part.

        Returns:
            numpy float32 array of shape (dim,), or None if model unavailable
        """
        if self.model is None:
            return None

        text = self._build_text(name, category, specs, status)
        embedding = self.model.encode([text], normalize_embeddings=True)[0]
        return embedding.astype(np.float32)

    def embed_query(self, query: str) -> Optional[np.ndarray]:
        """Embed a free-text search query."""
        if self.model is None:
            return None
        embedding = self.model.encode([query], normalize_embeddings=True)[0]
        return embedding.astype(np.float32)

    def embed_batch(self, texts: list[str]) -> Optional[np.ndarray]:
        """Embed multiple texts at once (more efficient)."""
        if self.model is None:
            return None
        embeddings = self.model.encode(texts, normalize_embeddings=True)
        return embeddings.astype(np.float32)

    @staticmethod
    def to_blob(embedding: np.ndarray) -> bytes:
        """Convert numpy array to bytes for SQLite BLOB storage."""
        return embedding.astype(np.float32).tobytes()

    @staticmethod
    def from_blob(blob: bytes, dim: int = 384) -> np.ndarray:
        """Restore numpy array from SQLite BLOB."""
        return np.frombuffer(blob, dtype=np.float32).reshape(dim)

    @staticmethod
    def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        """Compute cosine similarity between two vectors."""
        dot = np.dot(a, b)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(dot / (norm_a * norm_b))
