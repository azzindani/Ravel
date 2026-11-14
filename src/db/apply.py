"""Apply a delta to a loaded corpus: remove, add, replace derived rows, in one transaction.

    `ravel db apply <delta> --dsn ...`

Everything that decides what changes was decided when the delta was built (`db.delta`); this
applies it, and refuses to when it cannot be sure it is applying the right change to the right
database:

* **The version is checked inside the transaction, under a row lock.** The database must be at
  the delta's parent. A delta applied twice, or to a corpus that moved on, or beside another
  delta, fails the check rather than producing a corpus that is neither version.
* **New chunks go to the nearest existing centroid** and the clusters keep their generation.
  Assigning rows to centroids that did not change is the same routing structure by
  definition; how far the corpus has drifted from it is measured and reported, so a
  re-cluster is a decision made against a number.
* **Removed documents are deleted whole.** The provenance guard forbids editing a chunk's
  provenance, and a changed document is a new document, so nothing is ever updated in place.
* **The BM25 vocabulary stays fixed.** New chunks are encoded against the vocabulary the
  corpus was fitted with (the delta builder refuses a different one). Its IDF slowly ages as
  documents come and go; that is a re-fit, and a full rebuild.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from bundle.writer import read_bundle
from cluster import Clustering, metric_for
from db.layout import sync_layout
from db.verify import VerifyError, verify_ready
from load import DeltaSpec, LoadError, Placement, execute
from load.preflight import check_checksums

__all__ = ["ApplyError", "ApplyReport", "apply_delta"]

#: A new chunk is this much farther (cosine distance) from its centroid than the corpus's own
#: chunks are, on average, before a re-cluster is advised. A reading, not a gate.
DRIFT_ADVISED = 0.05


class ApplyError(RuntimeError):
    """The delta was not applied. Nothing was committed."""


@dataclass
class ApplyReport:
    corpus_id: str
    parent: str
    child: str
    added_docs: int = 0
    removed_docs: int = 0
    added_chunks: int = 0
    removed_chunks: int = 0
    placed: int = 0
    drift: dict[str, float] = field(default_factory=dict)
    seconds: dict[str, float] = field(default_factory=dict)
    ready: dict[str, int] = field(default_factory=dict)

    @property
    def recluster_advised(self) -> bool:
        return self.drift.get("gap", 0.0) > DRIFT_ADVISED


def _centroids(conn: Any, corpus_id: str) -> tuple[np.ndarray[Any, Any], int]:
    rows = conn.execute(
        "SELECT id, centroid::text, generation FROM clusters WHERE corpus_id = %s ORDER BY id",
        (corpus_id,),
    ).fetchall()
    if not rows:
        raise ApplyError(
            f"{corpus_id} has no clusters: it was not made ready by `ravel db prepare`"
        )
    matrix = np.asarray(
        [np.fromstring(text.strip("[]"), sep=",", dtype=np.float32) for _, text, _ in rows],
        dtype=np.float32,
    )
    return matrix, int(rows[0][2])


def _existing_distance(conn: Any, corpus_id: str, clustering: Clustering, sample: int) -> float:
    """Mean distance to the nearest centroid of a sample of the corpus's own placed chunks."""
    rows = conn.execute(
        "SELECT dense::text FROM chunks WHERE corpus_id = %s AND cluster_id IS NOT NULL "
        "ORDER BY random() LIMIT %s",
        (corpus_id, sample),
    ).fetchall()
    if not rows:
        return 0.0
    matrix = np.asarray(
        [np.fromstring(r[0].strip("[]"), sep=",", dtype=np.float32) for r in rows],
        dtype=np.float32,
    )
    return float(clustering.assign(matrix).distances.mean())


def _place_added(
    root: Path,
    document: dict[str, Any],
    clustering: Clustering,
    work: Path,
) -> tuple[Placement | None, float, int]:
    """The servable added chunks, each in its nearest cluster: `(placement, distance, n)`."""
    counts: dict[str, int] = document["inventory"]["counts"]
    shards = [root / n for n in sorted(counts) if n.startswith("vectors/")]
    if not shards:
        return None, 0.0, 0
    unusable: set[str] = set()
    quality = root / "doc_quality.parquet"
    skip: set[str] = set()
    if quality.exists():
        q = pq.read_table(str(quality), columns=["doc_id", "indexable"]).to_pylist()
        unusable = {r["doc_id"] for r in q if not r["indexable"]}
    if unusable:
        for n in sorted(counts):
            if n.startswith("chunks/"):
                t = pq.read_table(str(root / n), columns=["id", "doc_id"]).to_pylist()
                skip.update(r["id"] for r in t if r["doc_id"] in unusable)

    ids: list[str] = []
    assigned: list[np.ndarray[Any, Any]] = []
    distances: list[np.ndarray[Any, Any]] = []
    for shard in shards:
        table = pq.read_table(str(shard))
        chunk_ids = table.column("chunk_id").to_pylist()
        dim = table.schema.field("embedding").type.list_size
        flat = table.column("embedding").combine_chunks().values.to_numpy(zero_copy_only=False)
        matrix = flat.reshape(-1, dim)
        keep = np.fromiter((i not in skip for i in chunk_ids), dtype=bool, count=len(chunk_ids))
        if not keep.any():
            continue
        result = clustering.assign(matrix[keep].astype(np.float32))
        ids.extend(i for i, k in zip(chunk_ids, keep, strict=True) if k)
        assigned.append(result.cluster_ids)
        distances.append(result.distances)
    if not ids:
        return None, 0.0, 0
    cluster_ids = np.concatenate(assigned).astype(np.int32)
    target = work / "placement.parquet"
    pq.write_table(
        pa.table({"chunk_id": pa.array(ids, pa.string()), "cluster_id": pa.array(cluster_ids)}),
        str(target),
    )
    placement = Placement(
        assignments=target,
        centroids=clustering.centroids,
        row_counts=tuple(int(c) for c in np.bincount(cluster_ids, minlength=clustering.k)),
        generation=clustering.generation,
    )
    return placement, float(np.concatenate(distances).mean()), len(ids)


def apply_delta(
    delta: Path | str,
    dsn: str,
    *,
    on_step: Callable[[str, str], None] | None = None,
    on_rows: Callable[[str, int], None] | None = None,
    on_note: Callable[[str], None] | None = None,
) -> ApplyReport:
    """Apply one delta; return only when the database is ready at the new version."""
    import psycopg

    root = Path(delta)
    note = on_note or (lambda _m: None)
    document = read_bundle(root)
    block = document.get("delta")
    if not block:
        raise ApplyError(f"{root} is a bundle, not a delta: it has no `delta` block")
    checked = check_checksums(root, document)
    if checked.status.value != "ok":
        raise ApplyError(f"the delta's files do not match its manifest: {checked.detail}")

    corpus_id = str(document["corpus_id"])
    report = ApplyReport(
        corpus_id=corpus_id,
        parent=str(block["parent_manifest_sha256"]),
        child=str(block["child_manifest_sha256"]),
        added_docs=int(block["added_docs"]),
        removed_docs=int(block["removed_docs"]),
        added_chunks=int(block["added_chunks"]),
        removed_chunks=int(block["removed_chunks"]),
    )

    started = time.time()
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT manifest_sha256 FROM corpus_meta WHERE id = %s", (corpus_id,)
        ).fetchone()
        if row is None:
            raise ApplyError(f"{corpus_id} is not loaded: a delta changes a loaded corpus")
        if row[0] != report.parent:
            raise ApplyError(
                f"the database is at version {str(row[0])[:16]} and this delta is from "
                f"{report.parent[:16]}: apply the deltas in order, or rebuild"
            )
        work = Path(tempfile.mkdtemp(prefix="ravel-delta-"))
        try:
            placement: Placement | None = None
            if report.added_chunks:
                centroids, generation = _centroids(conn, corpus_id)
                clustering = Clustering(
                    centroids=centroids,
                    metric=metric_for(normalize=bool(document["dense"]["normalize"])),
                    seed=0,
                    sample_size=0,
                    iterations=0,
                    converged=True,
                    reseeded=0,
                    generation=generation,
                )
                placement, new_distance, report.placed = _place_added(
                    root, document, clustering, work
                )
                if placement is not None:
                    existing = _existing_distance(conn, corpus_id, clustering, 5000)
                    report.drift = {
                        "new": new_distance,
                        "existing": existing,
                        "gap": new_distance - existing,
                    }
                    note(
                        f"{report.placed:,} new chunks placed; mean distance to centroid "
                        f"{new_distance:.4f} vs {existing:.4f} for the corpus"
                    )
            report.seconds["place"] = time.time() - started

            started = time.time()
            try:
                execute(
                    root,
                    dsn,
                    include_indexes=False,
                    placement=placement,
                    delta=DeltaSpec(
                        parent_manifest_sha256=report.parent,
                        child_manifest_sha256=report.child,
                        child_source_manifest_sha256=str(block["child_source_manifest_sha256"]),
                        child_chunk_count=int(block["child_chunk_count"]),
                        added_docs=report.added_docs,
                        removed_docs=report.removed_docs,
                        added_chunks=report.added_chunks,
                        removed_chunks=report.removed_chunks,
                        run_id=str(document.get("run_id", "")),
                    ),
                    on_step=on_step,
                    on_rows=on_rows,
                )
            except LoadError as exc:
                raise ApplyError(str(exc)) from exc
            report.seconds["apply"] = time.time() - started
        finally:
            shutil.rmtree(work, ignore_errors=True)

    # ! After the delta's own transaction, ✗ inside it: `load.execute` owns that one. Until
    # this runs the dense arm reads the old layout -- a removed chunk's id is dropped by the
    # engine as a candidate with no row, and an added chunk is reachable by the lexical arms
    # only. `sync_layout` is idempotent, so a failure here is fixed by applying nothing and
    # re-running it, and `verify_ready` below refuses to call the database ready until it has.
    started = time.time()
    with psycopg.connect(dsn) as conn:
        synced = sync_layout(conn)
    note(f"layout: {synced.removed:,} vectors removed, {synced.added:,} added")
    report.seconds["layout"] = time.time() - started

    started = time.time()
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("ANALYZE chunks")
    report.seconds["analyze"] = time.time() - started

    started = time.time()
    try:
        report.ready = verify_ready(
            dsn,
            {
                "corpus_id": corpus_id,
                "chunk_count": int(block["child_chunk_count"]),
                "dense": document["dense"],
                "sparse": document.get("sparse"),
            },
        )
    except VerifyError as exc:
        raise ApplyError(
            f"applied, but the database is not ready at the new version: {exc}"
        ) from exc
    report.seconds["verify"] = time.time() - started
    return report


def describe(report: ApplyReport) -> str:
    return json.dumps(
        {
            "corpus": report.corpus_id,
            "from": report.parent[:16],
            "to": report.child[:16],
            "added_docs": report.added_docs,
            "removed_docs": report.removed_docs,
            "added_chunks": report.added_chunks,
            "removed_chunks": report.removed_chunks,
            "drift": report.drift,
        }
    )
