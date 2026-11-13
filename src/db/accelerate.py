"""What a loaded corpus needs beyond the load: the provenance guard and the optional indexes.

These were Vera's migrations 0002, 0003 and 0004, applied by hand after a corpus was
loaded. They belong with the loader: the schema is Ravel's (`CLAUDE.md` §5.7), and a
database that is "ready" must not depend on someone remembering three more scripts.

* **The provenance guard** is required. Provenance is the product and immutable
  (`CLAUDE.md` §5.3), and a trigger is the only form of that promise a later `UPDATE`
  cannot break. It forbids UPDATEs of the provenance columns; a changed document is
  deleted and inserted whole (see `db.apply`), never edited in place.
* **The text index (RUM) and the BM25 index (pg_search)** are accelerators. The engine
  probes for each and falls back to a slower scan without it, so a server that cannot build
  one still serves. They are attempted, and a failure is reported, never swallowed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["AcceleratorReport", "accelerate"]

PROVENANCE_GUARD = """
CREATE OR REPLACE FUNCTION chunks_provenance_is_immutable()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.source_url   IS DISTINCT FROM OLD.source_url
    OR NEW.source_title IS DISTINCT FROM OLD.source_title
    OR NEW.chapter      IS DISTINCT FROM OLD.chapter
    OR NEW.article      IS DISTINCT FROM OLD.article THEN
        RAISE EXCEPTION
            'provenance is immutable after ingest (chunk %)', OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS chunks_provenance_immutable ON chunks;
CREATE TRIGGER chunks_provenance_immutable
    BEFORE UPDATE ON chunks
    FOR EACH ROW
    EXECUTE FUNCTION chunks_provenance_is_immutable();
"""

#: `(name, sql)` for each optional accelerator, in the order they are tried.
OPTIONAL: tuple[tuple[str, str], ...] = (
    (
        "text index (rum)",
        "CREATE EXTENSION IF NOT EXISTS rum;"
        " CREATE INDEX IF NOT EXISTS chunks_tsv_rum"
        " ON chunks USING rum (tsv rum_tsvector_ops);",
    ),
    (
        "bm25 index (pg_search)",
        "CREATE EXTENSION IF NOT EXISTS pg_search;"
        " CREATE INDEX IF NOT EXISTS chunks_bm25 ON chunks USING bm25 (id, body)"
        " WITH (key_field = 'id'); ANALYZE chunks;",
    ),
)


@dataclass
class AcceleratorReport:
    applied: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)


def accelerate(dsn: str, *, conn: Any = None) -> AcceleratorReport:
    """Apply the provenance guard, then each optional accelerator the server can build."""
    import psycopg

    report = AcceleratorReport()
    own = conn is None
    connection = psycopg.connect(dsn, autocommit=True) if own else conn
    try:
        connection.execute(PROVENANCE_GUARD)
        report.applied.append("provenance guard")
        for name, sql in OPTIONAL:
            try:
                connection.execute(sql)
                report.applied.append(name)
            except psycopg.Error as exc:
                # Not fatal, and not silent: the engine falls back, slower, and the
                # operator is told which accelerator the database does not have.
                report.skipped[name] = str(exc).splitlines()[0]
    finally:
        if own:
            connection.close()
    return report
