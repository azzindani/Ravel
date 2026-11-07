"""Is this serving path the model the manifest declares?

A throughput backend (vLLM, TEI, a hosted endpoint) is a different implementation of the
model from the reference one in `transformers`, and the ways it can differ are all silent:
the pooling it applies, whether it appends the end-of-text token a last-token model expects,
the padding side, the precision, whether it truncates where the manifest says it does. Every
one of them returns a well-formed unit-norm vector of the right width. None fails a shape
check, and a corpus built on the wrong one ranks wrongly while every number looks fine.

So before a corpus is built on a serving engine, its vectors are compared with the reference
implementation's on real chunk text, and on queries, and the run stops if they disagree. The
comparison refuses to run a backend against itself, which would score 1.0 and certify
nothing (`preflight.check_independent_backends`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from embed.preflight import DEFAULT_MIN_COSINE, check_independent_backends
from embed.spec import Embedder

__all__ = ["BackendComparison", "compare_backends", "sample_chunk_texts"]


@dataclass(frozen=True, slots=True)
class BackendComparison:
    serving: str
    reference: str
    texts: int
    queries: int
    min_cosine: float
    mean_cosine: float
    worst_text: str
    floor: float

    @property
    def passed(self) -> bool:
        return self.min_cosine >= self.floor

    def describe(self) -> str:
        verdict = "agree" if self.passed else "DISAGREE"
        return (
            f"{self.serving} vs {self.reference}: {verdict} · min cosine "
            f"{self.min_cosine:.5f}, mean {self.mean_cosine:.5f} (floor {self.floor}) over "
            f"{self.texts} texts + {self.queries} queries"
        )


def _rows(matrix: Any) -> np.ndarray[Any, Any]:
    out: np.ndarray[Any, Any] = np.asarray(matrix, dtype=np.float32)
    return out


def compare_backends(
    serving: Embedder,
    reference: Embedder,
    texts: Sequence[str],
    queries: Sequence[str] = (),
    *,
    floor: float = DEFAULT_MIN_COSINE,
) -> BackendComparison:
    """Row-by-row cosine between two backends, documents and queries both.

    Raises rather than compares when the two are the same implementation: that is the
    unfalsifiable gate this module exists to avoid.
    """
    independent, why = check_independent_backends(serving, reference)
    if not independent:
        raise ValueError(why)
    if not texts:
        raise ValueError("nothing to compare: no texts")

    pairs: list[tuple[str, np.ndarray[Any, Any], np.ndarray[Any, Any]]] = []
    for label, a, b in (
        ("doc", serving.embed_documents(list(texts)), reference.embed_documents(list(texts))),
        *(
            [
                (
                    "query",
                    serving.embed_queries(list(queries)),
                    reference.embed_queries(list(queries)),
                )
            ]
            if queries
            else []
        ),
    ):
        left, right = _rows(a), _rows(b)
        if left.shape != right.shape:
            raise ValueError(f"{label} vectors differ in shape: {left.shape} vs {right.shape}")
        source = texts if label == "doc" else queries
        for i in range(left.shape[0]):
            pairs.append((source[i], left[i], right[i]))

    cosines = np.array(
        [
            float(np.dot(a, b) / max(np.linalg.norm(a) * np.linalg.norm(b), 1e-12))
            for _, a, b in pairs
        ]
    )
    worst = int(cosines.argmin())
    return BackendComparison(
        serving=type(serving).__name__,
        reference=type(reference).__name__,
        texts=len(texts),
        queries=len(queries),
        min_cosine=float(cosines.min()),
        mean_cosine=float(cosines.mean()),
        worst_text=pairs[worst][0][:80],
        floor=floor,
    )


def sample_chunk_texts(
    corpus: Any, workspace: Path | str, count: int, *, registry: Any = None
) -> list[str]:
    """`count` rendered chunk texts spread across the corpus, in a stable order.

    Every `stride`-th chunk of the first shards, so the sample is not the first document's
    boilerplate repeated, and the same call twice gives the same texts.
    """
    from chunk import iter_chunk_shards
    from chunk.derive import embed_text
    from spec import default_registry

    ref = corpus.extract.profile
    reg = registry or default_registry()
    profile = reg.get(ref) if ref else None
    texts: list[str] = []
    for index, chunk in enumerate(iter_chunk_shards(Path(workspace), corpus.id)):
        if index % 53 == 0:
            texts.append(embed_text(chunk, profile))
        if len(texts) >= count:
            break
    if not texts:  # a corpus smaller than the stride
        texts = [
            embed_text(c, profile)
            for _, c in zip(
                range(count), iter_chunk_shards(Path(workspace), corpus.id), strict=False
            )
        ]
    return texts
