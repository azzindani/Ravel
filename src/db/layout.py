"""The routed layout: every servable vector stored again, in cluster order, inline.

`chunk_vectors (cluster_id, id, dense)` is what the engine's dense arm scans when it is
present. It exists because `chunks` cannot be in cluster order: the cluster id is a
deployment decision made after the rows exist (`CLAUDE.md` §5.17), so a cluster's ~2,000
rows are scattered over the heap, and `halfvec(1024)` is 2 KB, which Postgres moves to
TOAST, so every vector is one more random read. Measured on the 1.7M-chunk corpus on
2 vCPU / 8 GB, a cold probe of 5 clusters:

    chunks          114K buffers   7,622 ms
    chunk_vectors     2K buffers      54 ms      (warm: 97 ms -> 7.7 ms)

Three rules make it correct rather than merely fast:

* **Servable rows only.** A chunk with an unusable text layer, or in no cluster, is not
  here, so the engine reads no `indexable` flag and never joins back to `chunks`.
* **`dense` is stored PLAIN.** Inline in the row, never compressed or moved to TOAST; a
  probe is then a few contiguous megabytes.
* **It is derived, never edited by hand.** A full build sorts by `(cluster_id, id)`; a
  delta deletes what left and appends what arrived (`sync`). Appended rows are out of
  order, which costs a delta-sized share of the speed until the next full build; a
  re-cluster rebuilds it anyway.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["LayoutReport", "build_layout", "layout_problems", "sync_layout"]

#: The rows the layout must hold, read from the state the engine serves.
SERVABLE = "indexable AND cluster_id IS NOT NULL AND dense IS NOT NULL"
#: The same predicate over `chunks c`, for the statements that also read `chunk_vectors v`.
SERVABLE_C = "c.indexable AND c.cluster_id IS NOT NULL AND c.dense IS NOT NULL"


@dataclass(frozen=True)
class LayoutReport:
    rows: int
    removed: int = 0
    added: int = 0


def _dense_type(conn: Any) -> str:
    """The column type of `chunks.dense`, e.g. `halfvec(1024)`: the layout matches it."""
    row = conn.execute(
        "SELECT format_type(atttypid, atttypmod) FROM pg_attribute"
        " WHERE attrelid = 'chunks'::regclass AND attname = 'dense' AND NOT attisdropped"
    ).fetchone()
    if row is None:
        raise RuntimeError("chunks has no dense column: there is nothing to lay out")
    return str(row[0])


def build_layout(conn: Any) -> LayoutReport:
    """Rebuild `chunk_vectors` from `chunks`, sorted by cluster. Idempotent."""
    kind = _dense_type(conn)
    conn.execute("DROP TABLE IF EXISTS chunk_vectors")
    conn.execute(
        f"CREATE TABLE chunk_vectors (cluster_id integer NOT NULL, id text NOT NULL,"
        f" dense {kind} NOT NULL)"
    )
    conn.execute("ALTER TABLE chunk_vectors ALTER COLUMN dense SET STORAGE PLAIN")
    conn.execute(
        "INSERT INTO chunk_vectors (cluster_id, id, dense)"
        f" SELECT cluster_id, id, dense FROM chunks WHERE {SERVABLE}"
        " ORDER BY cluster_id, id"
    )
    conn.execute("CREATE INDEX chunk_vectors_cluster_idx ON chunk_vectors (cluster_id)")
    conn.execute("CREATE UNIQUE INDEX chunk_vectors_id_idx ON chunk_vectors (id)")
    conn.execute("ANALYZE chunk_vectors")
    (rows,) = conn.execute("SELECT count(*) FROM chunk_vectors").fetchone()
    return LayoutReport(rows=int(rows))


def sync_layout(conn: Any) -> LayoutReport:
    """Bring `chunk_vectors` back to `chunks` after a delta: delete what left, add what came.

    Builds it whole when it does not exist yet, so a database prepared before the layout
    existed gains it on its first delta.
    """
    exists = conn.execute("SELECT to_regclass('chunk_vectors') IS NOT NULL").fetchone()
    if not (exists and exists[0]):
        return build_layout(conn)
    removed = conn.execute(
        "DELETE FROM chunk_vectors v WHERE NOT EXISTS ("
        " SELECT 1 FROM chunks c WHERE c.id = v.id AND c.cluster_id = v.cluster_id"
        f" AND {SERVABLE_C})"
    ).rowcount
    added = conn.execute(
        "INSERT INTO chunk_vectors (cluster_id, id, dense)"
        f" SELECT c.cluster_id, c.id, c.dense FROM chunks c WHERE {SERVABLE_C}"
        " AND NOT EXISTS (SELECT 1 FROM chunk_vectors v WHERE v.id = c.id)"
        " ORDER BY c.cluster_id, c.id"
    ).rowcount
    conn.execute("ANALYZE chunk_vectors")
    (rows,) = conn.execute("SELECT count(*) FROM chunk_vectors").fetchone()
    return LayoutReport(rows=int(rows), removed=int(removed), added=int(added))


def layout_problems(conn: Any) -> list[str]:
    """What is wrong with the layout, read from both tables. Empty means it is exact."""
    exists = conn.execute("SELECT to_regclass('chunk_vectors') IS NOT NULL").fetchone()
    if not (exists and exists[0]):
        return ["no chunk_vectors: the dense arm would scan chunks, ~140x slower cold"]
    (missing,) = conn.execute(
        f"SELECT count(*) FROM chunks c WHERE {SERVABLE_C}"
        " AND NOT EXISTS (SELECT 1 FROM chunk_vectors v"
        " WHERE v.id = c.id AND v.cluster_id = c.cluster_id)"
    ).fetchone()
    (extra,) = conn.execute(
        "SELECT count(*) FROM chunk_vectors v WHERE NOT EXISTS ("
        " SELECT 1 FROM chunks c WHERE c.id = v.id AND c.cluster_id = v.cluster_id"
        f" AND {SERVABLE_C})"
    ).fetchone()
    (storage,) = conn.execute(
        "SELECT attstorage FROM pg_attribute"
        " WHERE attrelid = 'chunk_vectors'::regclass AND attname = 'dense'"
    ).fetchone()
    problems: list[str] = []
    if missing:
        problems.append(f"{missing:,} servable chunks are missing from chunk_vectors")
    if extra:
        problems.append(f"{extra:,} rows in chunk_vectors are not servable chunks")
    if storage != "p":
        problems.append(f"chunk_vectors.dense is stored '{storage}', not PLAIN")
    return problems
