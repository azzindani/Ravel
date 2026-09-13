"""Oracle mode: brute force over a variant's vectors, with no routing at all.

`EVAL.md` §3 asks for two modes and the *gap* between them:

1. **Oracle** — brute-force search over the whole variant's vectors. Measures the ceiling
   the chunking and embedding achieve. This is the corpus-quality number, and it is the
   one Ravel is judged on, because routing is Vera's.
2. **Routed** — through Vera against a loaded bundle. Measures what a user actually gets.

The gap is the routing loss. Reporting one without the other has repeatedly sent people to
re-chunk when their real problem was routing — an expensive way to change nothing.

! Oracle mode must score with the **same similarity the engine uses**. If the ceiling is
measured by cosine and the engine ranks by something else, the gap is no longer routing
loss: it is routing loss plus a metric difference, and the two are indistinguishable in
one number. That is the same trap as `cluster/kmeans.py`'s metric mismatch, in the
measurement rather than in the structure.

Cost, honestly
--------------
Brute force is `n_queries x n_chunks x dim`. At 200 queries over 4M chunks at 1024
dimensions that is ~8 x 10^11 multiply-adds — minutes in BLAS, and the vectors do not fit
in RAM at full corpus scale. So the index streams shards and keeps a running top-k rather
than materialising a score matrix. Above roughly 10M chunks, oracle mode should run on a
stratified sample and say so in the report, because a ceiling measured on 2% of the corpus
is still a ceiling and a job that is OOM-killed is not a number.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from evaluate.labels import QuerySet
from evaluate.metrics import Candidate

__all__ = ["OracleIndex", "search", "top_k"]

#: Chunks scored per block. The temporary is `n_queries x block` floats.
BLOCK = 65_536


@dataclass
class OracleIndex:
    """Chunk identities plus their vectors, streamed rather than held.

    `docs` and `locators` are carried beside the vectors because the scorer compares
    citations, not chunk ids (`EVAL.md` §1). An index that held only ids would force the
    scorer back into the chunks table for every candidate, once per query.
    """

    ids: list[str] = field(default_factory=list)
    docs: list[str] = field(default_factory=list)
    locators: list[str] = field(default_factory=list)
    vectors: list[np.ndarray] = field(default_factory=list)

    def add(
        self,
        ids: Sequence[str],
        docs: Sequence[str],
        locators: Sequence[str],
        matrix: np.ndarray,
    ) -> None:
        block = np.asarray(matrix, dtype=np.float32)
        if not (len(ids) == len(docs) == len(locators) == block.shape[0]):
            raise ValueError(
                f"{len(ids)} ids, {len(docs)} docs, {len(locators)} locators and "
                f"{block.shape[0]} vectors — a misaligned block silently scores one "
                f"chunk's vector against another chunk's citation"
            )
        self.ids.extend(ids)
        self.docs.extend(docs)
        self.locators.extend(locators)
        self.vectors.append(_unit(block))

    def __len__(self) -> int:
        return len(self.ids)

    @property
    def matrix(self) -> np.ndarray:
        if not self.vectors:
            raise ValueError("the oracle index is empty; nothing was added to it")
        if len(self.vectors) > 1:
            self.vectors = [np.vstack(self.vectors)]
        return self.vectors[0]

    def blocks(self) -> Iterator[tuple[int, np.ndarray]]:
        matrix = self.matrix
        for start in range(0, matrix.shape[0], BLOCK):
            yield start, matrix[start : start + BLOCK]

    def candidate(self, index: int) -> Candidate:
        return Candidate(
            chunk_id=self.ids[index], doc=self.docs[index], locator=self.locators[index]
        )


def _unit(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    np.maximum(norms, 1e-12, out=norms)
    unit: np.ndarray = matrix / norms
    return unit


def top_k(
    queries: np.ndarray, index: OracleIndex, k: int = 50
) -> list[list[tuple[int, float]]]:
    """Brute-force cosine top-k per query, streamed in blocks.

    ! A running heap rather than one big `argsort`. Sorting a `n_queries x n_chunks` score
    matrix needs the matrix, and at corpus scale the matrix is the thing that does not fit.
    """
    probes = _unit(np.asarray(queries, dtype=np.float32))
    best: list[list[tuple[float, int]]] = [[] for _ in range(probes.shape[0])]

    for offset, block in index.blocks():
        scores = probes @ block.T
        take = min(k, scores.shape[1])
        # argpartition, not argsort: the order inside the top k is fixed afterwards, and
        # partitioning a block is linear where sorting it is not.
        cut = np.argpartition(-scores, take - 1, axis=1)[:, :take]
        for q in range(probes.shape[0]):
            heap = best[q]
            for column in cut[q]:
                item = (float(scores[q, column]), offset + int(column))
                if len(heap) < k:
                    heapq.heappush(heap, item)
                elif item[0] > heap[0][0]:
                    heapq.heapreplace(heap, item)

    return [
        [(i, score) for score, i in sorted(heap, key=lambda pair: -pair[0])] for heap in best
    ]


def search(
    queries: QuerySet,
    query_vectors: np.ndarray,
    index: OracleIndex,
    *,
    k: int = 50,
) -> dict[str, list[Candidate]]:
    """Ranked candidates per qid, for `evaluate()`.

    `query_vectors` is in the same row order as `queries`, and that is asserted rather than
    trusted: an off-by-one here scores every query against its neighbour's answers and
    produces a number that is plausibly low rather than obviously wrong.
    """
    matrix = np.asarray(query_vectors, dtype=np.float32)
    if matrix.shape[0] != len(queries):
        raise ValueError(
            f"{matrix.shape[0]} query vectors for {len(queries)} queries — a misalignment "
            f"here scores every query against the next one's answers and looks like a "
            f"merely disappointing result"
        )
    if len(index) == 0:
        return {query.qid: [] for query in queries}

    ranked = top_k(matrix, index, k=k)
    return {
        query.qid: [index.candidate(i) for i, _ in hits]
        for query, hits in zip(queries, ranked, strict=True)
    }


def build_index(rows: Iterable[tuple[str, str, str, np.ndarray]]) -> OracleIndex:
    """`(chunk_id, doc identifier, locator, vector)` tuples into an index."""
    index = OracleIndex()
    batch: list[tuple[str, str, str, np.ndarray]] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= 4096:
            _flush(index, batch)
            batch = []
    if batch:
        _flush(index, batch)
    return index


def _flush(index: OracleIndex, batch: list[tuple[str, str, str, np.ndarray]]) -> None:
    index.add(
        [r[0] for r in batch],
        [r[1] for r in batch],
        [r[2] for r in batch],
        np.stack([np.asarray(r[3], dtype=np.float32) for r in batch]),
    )


def sampling_note(chunks: int, sampled: int) -> dict[str, Any]:
    """What an oracle run over a sample is allowed to claim.

    A ceiling measured on 2% of the corpus is still a ceiling — it is just a ceiling with a
    confidence interval, and the report has to say which.
    """
    if sampled >= chunks:
        return {"sampled": False, "chunks": chunks}
    return {
        "sampled": True,
        "chunks": chunks,
        "scored": sampled,
        "share": round(sampled / chunks, 6) if chunks else 0.0,
        "caveat": (
            f"oracle recall measured against {sampled:,} of {chunks:,} chunks. Recall can "
            f"only be optimistic relative to the full corpus for distractors and "
            f"pessimistic for answers outside the sample; compare against another run at "
            f"the same sample size, never against a full-corpus number."
        ),
    }
