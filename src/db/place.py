"""Fit the routing structure from a bundle's own vectors, before anything is loaded.

The bundle ships with no cluster assignment (`CLAUDE.md` §5.17): how many clusters there
are and how the table is laid out are deployment decisions. The database that is deployed
does need them, so this fits them from the bundle's vectors and hands the loader a
`Placement`, and the rows are written with their cluster in the same pass that writes them.

Only chunks the engine can serve are clustered: a chunk of a document whose text layer is
unusable (`doc_quality.indexable = false`) is in no cluster, and the engine never scans it.

Centroids are fitted on a seeded sample and every chunk is then assigned to its nearest
centroid, in the metric the embedder declares. The fit is the expensive half; fitting on
`SAMPLE_PER_CLUSTER` rows per centroid gives each centroid a few hundred members to
describe, which is what lets the full corpus be placed in minutes instead of iterating
k-means over every row.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from cluster import fit, metric_for, sample_indices, suggest_k
from load.plan import Placement

__all__ = ["SAMPLE_PER_CLUSTER", "place"]

#: Rows fitted per centroid. A centroid described by fewer than this is described by noise.
SAMPLE_PER_CLUSTER = 250

#: Rows assigned per matrix product, so the corpus is never one matrix in memory twice.
ASSIGN_BATCH = 50_000


def _shards(root: Path, prefix: str, counts: dict[str, int]) -> list[Path]:
    return [root / name for name in sorted(counts) if name.startswith(prefix)]


def place(
    bundle: Path | str,
    work: Path | str,
    *,
    rows_per_cluster: int = 2000,
    seed: int = 42,
    max_iter: int = 30,
    on_progress: Callable[[str], None] | None = None,
) -> Placement:
    """Fit centroids from the bundle's vectors and assign every servable chunk to one.

    Writes `placement.parquet` under `work` (the loader reads it) and returns the
    `Placement` that names it.
    """
    root = Path(bundle)
    note = on_progress or (lambda _msg: None)
    document: dict[str, Any] = json.loads((root / "manifest.json").read_text("utf-8"))
    counts: dict[str, int] = document["inventory"]["counts"]
    normalise = bool(document["dense"]["normalize"])

    # -- which chunks the engine will serve ------------------------------------------
    unusable: set[str] = set()
    quality = root / "doc_quality.parquet"
    if quality.exists():
        table = pq.read_table(str(quality), columns=["doc_id", "indexable"])
        unusable = {
            d
            for d, ok in zip(
                table.column("doc_id").to_pylist(),
                table.column("indexable").to_pylist(),
                strict=True,
            )
            if not ok
        }
    skip_ids: set[str] = set()
    if unusable:
        for shard in _shards(root, "chunks/", counts):
            t = pq.read_table(str(shard), columns=["id", "doc_id"])
            skip_ids.update(
                i
                for i, d in zip(
                    t.column("id").to_pylist(), t.column("doc_id").to_pylist(), strict=True
                )
                if d in unusable
            )

    # -- the vectors of those chunks, kept at the half precision they are stored in -----
    ids: list[str] = []
    blocks: list[np.ndarray] = []
    for shard in _shards(root, "vectors/", counts):
        t = pq.read_table(str(shard))
        chunk_ids = t.column("chunk_id").to_pylist()
        dim = t.schema.field("embedding").type.list_size
        flat = t.column("embedding").combine_chunks().values.to_numpy(zero_copy_only=False)
        matrix = flat.reshape(-1, dim)
        keep = np.fromiter(
            (i not in skip_ids for i in chunk_ids), dtype=bool, count=len(chunk_ids)
        )
        ids.extend(i for i, k in zip(chunk_ids, keep, strict=True) if k)
        blocks.append(matrix[keep])
    if not ids:
        raise ValueError("no servable chunks to place")
    vectors = np.vstack(blocks)
    del blocks
    n = vectors.shape[0]
    note(f"{n:,} servable chunks ({len(skip_ids):,} left out: unusable text layer)")

    # -- fit on a seeded sample, then assign every chunk ----------------------------------
    k = suggest_k(n, rows_per_cluster=rows_per_cluster)
    sample = sample_indices(n, min(n, k * SAMPLE_PER_CLUSTER), seed=seed)
    note(f"fitting {k:,} centroids on {len(sample):,} rows")
    fitted = fit(
        vectors[sample].astype(np.float32),
        k,
        metric=metric_for(normalize=normalise),
        seed=seed,
        max_iter=max_iter,
    )
    fitted = dataclasses.replace(fitted, sample_size=len(sample))
    note(f"fitted in {fitted.iterations} iterations (converged: {fitted.converged})")

    assigned = np.empty(n, dtype=np.int32)
    for start in range(0, n, ASSIGN_BATCH):
        batch = vectors[start : start + ASSIGN_BATCH].astype(np.float32)
        assigned[start : start + ASSIGN_BATCH] = fitted.assign(batch).cluster_ids
    row_counts = np.bincount(assigned, minlength=fitted.k)
    note(
        f"placed: smallest cluster {int(row_counts.min()):,}, "
        f"largest {int(row_counts.max()):,}, empty {int((row_counts == 0).sum())}"
    )

    out = Path(work)
    out.mkdir(parents=True, exist_ok=True)
    target = out / "placement.parquet"
    pq.write_table(
        pa.table({"chunk_id": pa.array(ids, pa.string()), "cluster_id": pa.array(assigned)}),
        str(target),
    )
    return Placement(
        assignments=target,
        centroids=fitted.centroids,
        row_counts=tuple(int(c) for c in row_counts),
        generation=fitted.generation,
    )
