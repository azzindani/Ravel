"""Is this database ready to serve? Asserted from the database, against the manifest.

A check that replays how the database was built certifies that process's own mistakes
(`CLAUDE.md` §5.15), so each one here reads the state the engine will read and compares it
with the sealed manifest.
"""

from __future__ import annotations

from typing import Any

from db.layout import layout_problems
from db.routing import parent_problems

__all__ = ["VerifyError", "verify_ready"]


class VerifyError(RuntimeError):
    """The database is not ready. What it says is what is wrong."""


def _one(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> tuple[Any, ...]:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        raise VerifyError(f"a query that always returns a row returned none: {sql[:60]}")
    return tuple(row)


def verify_ready(dsn: str, document: dict[str, Any]) -> dict[str, int]:
    import psycopg

    corpus_id = str(document["corpus_id"])
    expect = int(document["chunk_count"])
    dim = int(document["dense"]["dim"])
    problems: list[str] = []
    with psycopg.connect(dsn) as conn:
        total, dense, sparse, servable, unplaced, placed = _one(
            conn,
            "SELECT count(*), count(*) FILTER (WHERE dense IS NOT NULL),"
            " count(*) FILTER (WHERE sparse IS NOT NULL),"
            " count(*) FILTER (WHERE indexable),"
            " count(*) FILTER (WHERE indexable AND cluster_id IS NULL),"
            " count(*) FILTER (WHERE cluster_id IS NOT NULL)"
            " FROM chunks WHERE corpus_id = %s",
            (corpus_id,),
        )
        clusters, covered = _one(
            conn,
            "SELECT count(*), coalesce(sum(row_count), 0) FROM clusters WHERE corpus_id = %s",
            (corpus_id,),
        )
        canaries, canary_dim = _one(
            conn,
            "SELECT count(*), max(vector_dims(dense)) FROM corpus_canary WHERE corpus_id = %s",
            (corpus_id,),
        )
        (guard,) = _one(
            conn,
            "SELECT count(*) FROM pg_trigger WHERE tgname = 'chunks_provenance_immutable'",
        )
        problems.extend(layout_problems(conn))
        problems.extend(parent_problems(conn, corpus_id))
    if total != expect:
        problems.append(f"{total:,} chunks in the database, the manifest says {expect:,}")
    if dense != total:
        problems.append(f"{total - dense:,} chunks have no dense vector")
    if document.get("sparse") and sparse != total:
        problems.append(f"{total - sparse:,} chunks have no sparse vector")
    if clusters == 0:
        problems.append("no clusters: the engine has nothing to route by")
    if unplaced:
        problems.append(f"{unplaced:,} servable chunks are in no cluster")
    if placed != covered:
        problems.append(f"clusters say {covered:,} rows, {placed:,} chunks are placed")
    if not canaries:
        problems.append(
            "no canary row: the engine cannot check its embedder against the recipe"
        )
    elif canary_dim != dim:
        problems.append(f"the canary is {canary_dim}-dimensional, the corpus is {dim}")
    if not guard:
        problems.append("the provenance guard is not installed")
    if problems:
        raise VerifyError("; ".join(problems))
    return {
        "chunks": int(total),
        "servable": int(servable),
        "placed": int(placed),
        "clusters": int(clusters),
    }
