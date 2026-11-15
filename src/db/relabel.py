"""Apply a relabelled bundle to the database already serving its parent.

    ravel db relabel <relabelled bundle> --dsn ...

`bundle.relabel` decided every change and wrote it down (`relabel.parquet`, plus the new
vectors in the bundle's own shards); this applies exactly that, in one transaction:

* **The version is checked under a row lock**, as `db apply` does: the database must hold
  the relabelled bundle's parent. Applied twice it is a no-op, because the database is
  already at the child; applied to anything else it refuses.
* **Every changed row must still read its old label.** A database that drifted from the
  bundle is not patched into something neither of them describes.
* **The provenance guard stays on.** It forbids an `UPDATE` of a chunk's labels
  (`migrations/0002_provenance_immutable.sql` in Vera), so the rows are deleted and
  inserted again with the new chapter and vector, as a delta replaces a document. The
  guard is never disabled, and no other column of the row changes.
* **`chunk_vectors` follows**: the engine's dense arm scans it, and a vector updated in
  `chunks` only would be invisible to search. A row keeps its cluster: one relabel moves a
  vector by a few hundredths of cosine, which `db apply` would also place in place.
* **The change is recorded** in `corpus_versions` (parent, child, rows replaced) and
  `corpus_meta` takes the child's manifest hash and profile.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pyarrow.parquet as pq

from bundle.relabel import _vectors
from bundle.writer import read_bundle
from load.preflight import check_checksums

__all__ = ["RelabelApplyError", "RelabelApplied", "apply_relabel"]


class RelabelApplyError(RuntimeError):
    pass


@dataclass
class RelabelApplied:
    corpus_id: str
    parent: str
    child: str
    rows: int
    already: bool = False


def apply_relabel(
    bundle: Path | str, dsn: str, *, on_note: Callable[[str], None] | None = None
) -> RelabelApplied:
    import psycopg

    note = on_note or (lambda _m: None)
    root = Path(bundle)
    document = read_bundle(root)
    block = document.get("relabel")
    if not block:
        raise RelabelApplyError(f"{root} has no `relabel` block: not a relabelled bundle")
    checked = check_checksums(root, document)
    if checked.status.value != "ok":
        raise RelabelApplyError(
            f"the bundle's files do not match its manifest: {checked.detail}"
        )

    corpus_id = str(document["corpus_id"])
    parent, child = str(block["parent_manifest_sha256"]), str(document["manifest_sha256"])
    plan = pq.read_table(root / "relabel.parquet").to_pydict()
    ids: list[str] = plan["id"]
    result = RelabelApplied(corpus_id, parent, child, len(ids))
    vectors = _vectors(root, set(ids))
    missing = [i for i in ids if i not in vectors]
    if missing:
        raise RelabelApplyError(
            f"{len(missing)} relabelled chunks have no vector in the bundle"
        )

    with psycopg.connect(dsn) as conn, conn.transaction():
        row = conn.execute(
            "SELECT manifest_sha256 FROM corpus_meta WHERE id = %s FOR UPDATE", (corpus_id,)
        ).fetchone()
        if row is None:
            raise RelabelApplyError(f"{corpus_id} is not loaded")
        if row[0] == child:
            note("already at this version; nothing to do")
            result.already = True
            return result
        if row[0] != parent:
            raise RelabelApplyError(
                f"the database is at {str(row[0])[:16]} and this relabel is from {parent[:16]}"
            )

        conn.execute(
            "CREATE TEMP TABLE relabel_plan (id text PRIMARY KEY, old_chapter text,"
            " new_chapter text NOT NULL, dense text NOT NULL) ON COMMIT DROP"
        )
        with conn.cursor().copy(
            "COPY relabel_plan (id, old_chapter, new_chapter, dense) FROM STDIN"
        ) as copy:
            for cid, old, new in zip(
                ids, plan["old_chapter"], plan["new_chapter"], strict=True
            ):
                vec = "[" + ",".join(f"{x:.6g}" for x in vectors[cid].tolist()) + "]"
                copy.write_row((cid, old, new, vec))
        found = conn.execute(
            "SELECT count(*) FROM chunks c JOIN relabel_plan p ON p.id = c.id"
            " AND c.chapter IS NOT DISTINCT FROM p.old_chapter"
        ).fetchone()
        matched = int(found[0]) if found else 0
        if matched != len(ids):
            raise RelabelApplyError(
                f"{len(ids) - matched} of {len(ids)} rows no longer read their old label: the "
                f"database drifted from the bundle's parent"
            )

        # Every stored column, ✗ generated ones (`tsv` is derived from `body`).
        cols = [
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'chunks'"
                " AND table_schema = current_schema() AND is_generated = 'NEVER'"
                " ORDER BY ordinal_position"
            ).fetchall()
        ]
        listed = ", ".join(f'"{c}"' for c in cols)
        picked = ", ".join(
            "p.new_chapter"
            if c == "chapter"
            else "p.dense::halfvec"
            if c == "dense"
            else f'c."{c}"'
            for c in cols
        )
        conn.execute(
            f"CREATE TEMP TABLE relabel_rows ON COMMIT DROP AS SELECT {picked}"
            " FROM chunks c JOIN relabel_plan p ON p.id = c.id"
        )
        conn.execute("DELETE FROM chunks c USING relabel_plan p WHERE c.id = p.id")
        conn.execute(f"INSERT INTO chunks ({listed}) SELECT * FROM relabel_rows")
        moved = conn.execute(
            "UPDATE chunk_vectors v SET dense = p.dense::halfvec FROM relabel_plan p"
            " WHERE v.id = p.id"
        ).rowcount
        note(f"{len(ids):,} chunks replaced, {moved:,} vectors updated in chunk_vectors")
        conn.execute(
            "UPDATE corpus_meta SET manifest_sha256 = %s, profile_ref = %s WHERE id = %s",
            (child, str(block["profile"]), corpus_id),
        )
        conn.execute(
            "INSERT INTO corpus_versions (corpus_id, manifest_sha256, parent_sha256,"
            " added_docs, removed_docs, added_chunks, removed_chunks)"
            " VALUES (%s, %s, %s, 0, 0, %s, %s)",
            (corpus_id, child, parent, len(ids), len(ids)),
        )
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("ANALYZE chunks")
    return result
