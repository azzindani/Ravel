"""Two-level routing: parent centroids over the leaf centroids, so the engine's set is O(√k).

Vera holds every cluster centroid in memory and scans all of them per query. `k` grows
with the corpus (~2,000 rows a cluster), so that set grows with it: 30 MB of engine at
1.7M rows, 184 MB at 20.6M, and ~0.9 GB at 100M against a 512 MB container (Vera
`docs/SCALING.md` §1). The fix is the one routing already applies to rows: do not look
at what is far away.

    cluster_parents (id, corpus_id, centroid, leaves)   ~√k rows, held hot by the engine
    clusters.parent_id                                  every leaf under one parent

The engine picks the few nearest parents, then asks Postgres for the nearest leaves
among their children: ids and scores come back, never a vector (Vera `CLAUDE.md` §7.5).

* **Fitted over the leaf centroids, ✗ the rows.** The leaves are what a query is routed
  to; grouping them is a k-means over k points, seconds even at 50K leaves.
* **Below `MIN_LEAVES` there is no parent level.** A few hundred centroids cost nothing
  to scan, and a second routing decision is a second chance to miss.
* **Deterministic.** Seeded, so the same leaves always group the same way.
* **Derived, like `chunk_vectors`.** Rebuilt by `prepare`; a delta assigns new rows to
  existing leaves and so leaves the parent level untouched.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from cluster import fit, metric_for

__all__ = ["MIN_LEAVES", "ParentReport", "build_parents", "parent_problems"]

#: Fewer leaves than this and the engine scans them all; a parent level only adds a miss.
MIN_LEAVES = 256


@dataclass(frozen=True)
class ParentReport:
    parents: int
    leaves: int
    largest: int


def build_parents(
    conn: Any, corpus_id: str, *, normalize: bool = True, seed: int = 42, parents: int = 0
) -> ParentReport | None:
    """Fit the parent level and write it. `parents=0` picks √k. `None` when too few leaves."""
    rows = conn.execute(
        "SELECT id, centroid::text FROM clusters WHERE corpus_id = %s ORDER BY id",
        (corpus_id,),
    ).fetchall()
    conn.execute("DROP TABLE IF EXISTS cluster_parents")
    conn.execute("ALTER TABLE clusters ADD COLUMN IF NOT EXISTS parent_id integer")
    conn.execute("UPDATE clusters SET parent_id = NULL WHERE corpus_id = %s", (corpus_id,))
    if len(rows) < MIN_LEAVES:
        return None
    ids = [int(r[0]) for r in rows]
    leaves = np.array([json.loads(r[1]) for r in rows], dtype=np.float32)
    k = parents or max(2, round(math.sqrt(len(ids))))
    clustering = fit(leaves, k, metric=metric_for(normalize=normalize), seed=seed, max_iter=50)
    assigned = clustering.assign(leaves).cluster_ids.astype(int)

    dim = leaves.shape[1]
    conn.execute(
        f"CREATE TABLE cluster_parents (id integer PRIMARY KEY, corpus_id text NOT NULL,"
        f" centroid halfvec({dim}) NOT NULL, leaves integer NOT NULL)"
    )
    sizes = np.bincount(assigned, minlength=k)
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO cluster_parents (id, corpus_id, centroid, leaves)"
            " VALUES (%s, %s, %s::halfvec, %s)",
            [
                (
                    p,
                    corpus_id,
                    "[" + ",".join(f"{x:.7g}" for x in clustering.centroids[p]) + "]",
                    int(sizes[p]),
                )
                for p in range(k)
            ],
        )
        cur.execute(
            "CREATE TEMP TABLE parent_stage (id integer, parent_id integer) ON COMMIT DROP"
        )
        cur.executemany(
            "INSERT INTO parent_stage VALUES (%s, %s)",
            list(zip(ids, assigned.tolist(), strict=True)),
        )
        cur.execute(
            "UPDATE clusters c SET parent_id = s.parent_id"
            " FROM parent_stage s WHERE c.id = s.id"
        )
    conn.execute("CREATE INDEX IF NOT EXISTS clusters_parent_idx ON clusters (parent_id)")
    conn.execute("ANALYZE clusters")
    conn.execute("ANALYZE cluster_parents")
    return ParentReport(parents=k, leaves=len(ids), largest=int(sizes.max()))


def parent_problems(conn: Any, corpus_id: str) -> list[str]:
    """What is wrong with the parent level, read from the database. Empty means sound."""
    has = conn.execute("SELECT to_regclass('cluster_parents') IS NOT NULL").fetchone()
    if not (has and has[0]):
        return []  # optional: the engine scans every leaf without it
    orphans, unknown = conn.execute(
        "SELECT count(*) FILTER (WHERE parent_id IS NULL),"
        " count(*) FILTER (WHERE parent_id IS NOT NULL AND parent_id NOT IN"
        " (SELECT id FROM cluster_parents)) FROM clusters WHERE corpus_id = %s",
        (corpus_id,),
    ).fetchone()
    problems: list[str] = []
    if orphans:
        problems.append(
            f"{orphans:,} clusters have no parent: the engine could never reach them"
        )
    if unknown:
        problems.append(f"{unknown:,} clusters name a parent that does not exist")
    return problems
