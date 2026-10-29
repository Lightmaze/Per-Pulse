"""Deterministic feature helpers shared by the sideband learning components.

- ``stable_id_embedding``: a fixed pseudo-embedding derived from an id's
  hash — gives every engram a caller-identity vector without training
  (delegation router input) and stays stable across restarts.
- ``random_project``: seeded random projection to a fixed dimension —
  task embeddings differ per provider (mock 256-d, OpenAI 1536-d), the
  MLP input must not. The projection matrix is derived deterministically
  from (in_dim, out_dim, seed), so no state needs persisting.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

import numpy as np

_PROJECTION_CACHE: dict[tuple[int, int, int], np.ndarray] = {}


def stable_id_embedding(identifier: str, dim: int) -> np.ndarray:
    """Unit-norm vector derived from the identifier's SHA-256."""
    digest = hashlib.sha256(identifier.encode()).digest()
    seed = int.from_bytes(digest[:8], "big")
    rng = np.random.default_rng(seed)
    v = rng.normal(0.0, 1.0, dim)
    norm = np.linalg.norm(v)
    return v / norm if norm > 0 else v


def stable_text_embedding(text: str, dim: int = 256) -> np.ndarray:
    """Return a local, deterministic feature-hash embedding for task text.

    This is deliberately not a semantic/provider embedding.  It gives the
    durable delegation path a restart-stable, zero-network task feature while
    preserving a useful amount of lexical overlap (including CJK character
    n-grams).  Provider embeddings may still be used by legacy callers that
    already have an explicit embedding authority.
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    if not isinstance(dim, int) or isinstance(dim, bool) or dim < 1:
        raise ValueError("dim must be an integer >= 1")

    normalized = unicodedata.normalize("NFKC", text).casefold().strip()
    vector = np.zeros(dim, dtype=float)
    if not normalized:
        return vector

    # Bound feature extraction independently of caller-controlled task size.
    normalized = normalized[:4096]
    compact = re.sub(r"\s+", " ", normalized)
    features: list[str] = []
    for token in re.findall(r"\w+", compact, flags=re.UNICODE):
        features.append(f"w:{token}")
        padded = f"^{token}$"
        for size in (2, 3, 4):
            features.extend(
                f"c{size}:{padded[index:index + size]}"
                for index in range(max(0, len(padded) - size + 1))
            )

    # Punctuation-separated CJK or short fragments can otherwise disappear
    # from word-token features, so retain bounded whole-text character grams.
    dense = re.sub(r"\s+", "", compact)
    for size in (2, 3):
        features.extend(
            f"t{size}:{dense[index:index + size]}"
            for index in range(max(0, len(dense) - size + 1))
        )

    for feature in features[:16384]:
        digest = hashlib.sha256(feature.encode("utf-8")).digest()
        slot = int.from_bytes(digest[:8], "big") % dim
        sign = 1.0 if digest[8] & 1 else -1.0
        vector[slot] += sign

    norm = np.linalg.norm(vector)
    return vector / norm if norm > 0 else vector


def random_project(vector, out_dim: int, *, seed: int = 13) -> np.ndarray:
    """Project a vector of any dimension to out_dim deterministically."""
    v = np.asarray(vector, dtype=float)
    in_dim = v.shape[0]
    if in_dim == out_dim:
        return v
    key = (in_dim, out_dim, seed)
    matrix = _PROJECTION_CACHE.get(key)
    if matrix is None:
        rng = np.random.default_rng(seed + in_dim * 31 + out_dim * 7)
        matrix = rng.normal(0.0, 1.0 / np.sqrt(out_dim), (in_dim, out_dim))
        _PROJECTION_CACHE[key] = matrix
    projected = v @ matrix
    norm = np.linalg.norm(projected)
    return projected / norm if norm > 0 else projected
