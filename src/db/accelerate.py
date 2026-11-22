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
* **The region scope** is Vera's migration 0007 (Vera `ROADMAP.md` Stage 3). The BM25
  index carries `enacting_body` as a literal field, and `corpus_regions` lists the region
  names a question is matched against. Vera's region arm needs both and stays off without
  them. A pg_search too old for the literal field still gets the plain index, never none.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["AcceleratorReport", "accelerate", "refresh_regions"]

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

#: The BM25 index with `enacting_body` as a literal field, which Vera's region arm filters
#: on. ! An index built before the field existed is replaced: `IF NOT EXISTS` alone would
#: keep it, and the arm would stay off on a database that looks prepared. In one implicit
#: transaction, so a server that cannot build the field keeps its old index.
BM25_WITH_REGION = (
    "CREATE EXTENSION IF NOT EXISTS pg_search;"
    " DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'chunks_bm25'"
    " AND schemaname = current_schema()"
    " AND indexdef NOT LIKE '%enacting_body%') THEN DROP INDEX chunks_bm25; END IF; END $$;"
    " CREATE INDEX IF NOT EXISTS chunks_bm25 ON chunks USING bm25"
    " (id, body, (enacting_body::pdb.literal)) WITH (key_field = 'id'); ANALYZE chunks;"
)
BM25_PLAIN = (
    "CREATE EXTENSION IF NOT EXISTS pg_search;"
    " CREATE INDEX IF NOT EXISTS chunks_bm25 ON chunks USING bm25 (id, body)"
    " WITH (key_field = 'id'); ANALYZE chunks;"
)

#: `(name, sql variants)` for each optional accelerator, in the order they are tried. The
#: first variant the server accepts is applied; a later one is the fallback.
OPTIONAL: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "text index (rum)",
        (
            "CREATE EXTENSION IF NOT EXISTS rum SCHEMA public;"
            " CREATE INDEX IF NOT EXISTS chunks_tsv_rum"
            " ON chunks USING rum (tsv rum_tsvector_ops);",
        ),
    ),
    ("bm25 index (pg_search)", (BM25_WITH_REGION, BM25_PLAIN)),
)

#: The region names a question is matched against (Vera migration 0007). Generic words the
#: extractor left in the column (`KABUPATEN`, `KOTA`) and two-letter artefacts (`MA`, `RI`)
#: are excluded: they would scope every question that says "kabupaten".
REGIONS = """
DROP TABLE IF EXISTS corpus_regions;
CREATE TABLE corpus_regions AS
SELECT enacting_body, count(*)::bigint AS chunks
  FROM chunks
 WHERE enacting_body IS NOT NULL
   AND length(enacting_body) >= 4
   AND coalesce(regulation_type, '') !~ '(UNDANG|PEMERINTAH|PRESIDEN|MENTERI|BADAN)'
   AND enacting_body !~
       '^(REPUBLIK INDONESIA|INDONESIA|KABUPATEN|KOTA|PROVINSI|DAERAH|PEMERINTAH|NEGARA|N/A)$'
 GROUP BY enacting_body;
ALTER TABLE corpus_regions ADD PRIMARY KEY (enacting_body);
"""

REGION_FIELD = (
    "SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'chunks_bm25'"
    " AND schemaname = current_schema() AND indexdef LIKE '%enacting_body%')"
)


def refresh_regions(conn: Any) -> bool:
    """Rebuild `corpus_regions` when the BM25 index carries the region field.

    ! Without the field the list is not written: Vera reads the two as one capability,
    and a list it cannot search by would only look like one. Returns whether it was written.
    """
    row = conn.execute(REGION_FIELD).fetchone()
    if not (row and row[0]):
        return False
    conn.execute(REGIONS)
    return True


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
        for name, variants in OPTIONAL:
            errors: list[str] = []
            for n, sql in enumerate(variants):
                try:
                    connection.execute(sql)
                except psycopg.Error as exc:
                    errors.append(str(exc).splitlines()[0])
                    continue
                report.applied.append(name if n == 0 else f"{name}, fallback {n}")
                if n:
                    report.skipped[f"{name}, preferred"] = errors[0]
                break
            else:
                # Not fatal, and not silent: the engine falls back, slower, and the
                # operator is told which accelerator the database does not have.
                report.skipped[name] = errors[-1]
        try:
            if refresh_regions(connection):
                report.applied.append("region scope")
            else:
                report.skipped["region scope"] = "chunks_bm25 has no enacting_body field"
        except psycopg.Error as exc:
            report.skipped["region scope"] = str(exc).splitlines()[0]
    finally:
        if own:
            connection.close()
    return report
