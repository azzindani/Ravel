"""The load procedure as data: an ordered list of steps, generated from the manifest.

`BUNDLE.md` §6 gives the order. This module produces it as inspectable steps rather than
as a function that does them, for the same reason the schema is generated rather than
hand-written: a procedure you can print is a procedure someone can review before it runs
against a database holding a corpus that took GPU-days to build.

Two deliberate divergences from §6, both documented rather than accidental
--------------------------------------------------------------------------
§2 requires that: *"Divergences must be documented, never accidental."*

**1. Vectors are merged by insert, not by update.** §6 reads `COPY chunks → COPY vectors →
join/merge`, which suggests loading chunk rows and then updating them with their
embeddings. At 100M rows that update is the worst operation in the procedure: Postgres is
MVCC, so updating every row writes a second version of every row, doubling the table and
leaving the original as dead tuples for a `VACUUM` that has to read all of it back. So the
plan stages both sides into unlogged tables and does one `INSERT ... SELECT ... JOIN`. Same
result, one write per row instead of two.

**2. Counts are verified inside the transaction, before the commit.** §5's rule is "fail
any check → load nothing", and a check that runs after a commit cannot honour it — by then
the partial load exists and the only remedy is a truncate. Verifying inside the
transaction makes "load nothing" the literal behaviour: the count mismatch raises, the
transaction rolls back, and the database is exactly as it was.

**3. `corpus_meta` is stamped first, not last.** §6 ends with *"stamp manifest into
corpus_meta"*, which reads naturally — the receipt goes on at the end. But the generated
schema declares `chunks.corpus_id REFERENCES corpus_meta(id)`, so a chunk inserted before
that row exists is a foreign key violation on the very first row of the load. Neither file
was wrong on its own; the contradiction existed only where they met, and nothing executed
both until `tools/ci_bundle_check.py` did.

The index build is the one thing that is *not* in the transaction, deliberately: it is
idempotent (`CREATE INDEX IF NOT EXISTS`), it is the slowest step by far, and holding a
transaction open across it serves no purpose once the data is committed and verified.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "DeltaSpec",
    "Placement",
    "Step",
    "load_plan",
    "copy_from",
    "quote_literal",
    "quote_ident",
]

#: `(staging column, SQL type, destination column in `chunks`)`, in parquet order.
#:
#: ! One table, so the three things that must agree cannot drift apart: what the shard
#: holds, what the staging table declares, and what the merge writes. They were three
#: separate lists until a real Postgres ran them together and every one of them turned out
#: to name columns the others did not have.
_STAGED: tuple[tuple[str, str, str], ...] = (
    ("id", "TEXT", "id"),
    ("doc_id", "TEXT", "doc_id"),
    ("body", "TEXT", "body"),
    ("token_count", "INTEGER", "token_count"),
    ("part_n", "INTEGER", "chunk_no"),
    ("seq", "INTEGER", "seq"),
    ("source_title", "TEXT", "source_title"),
    ("source_url", "TEXT", "source_url"),
    ("source_sha256", "TEXT", "source_sha256"),
    ("locator_page", "INTEGER", "locator_page"),
    ("locator_section", "TEXT", "locator_section"),
    ("heading_path", "TEXT", "heading_path"),
    ("identifier", "TEXT", "identifier"),
    ("regulation_type", "TEXT", "regulation_type"),
    ("enacting_body", "TEXT", "enacting_body"),
    ("regulation_number", "TEXT", "regulation_number"),
    ("year", "INTEGER", "year"),
    ("about", "TEXT", "about"),
    ("effective_date", "TEXT", "effective_date"),
    ("chapter", "TEXT", "chapter"),
    ("article", "TEXT", "article"),
    ("chunker", "TEXT", "chunker"),
    ("chunker_version", "TEXT", "chunker_version"),
    ("config_hash", "TEXT", "config_hash"),
    ("profile", "TEXT", "profile"),
)

CHUNK_COLUMNS: tuple[str, ...] = tuple(source for source, _, _ in _STAGED)


@dataclass(frozen=True)
class Placement:
    """Where every chunk goes in the routing structure, decided before the load.

    `assignments` is a parquet file of `(chunk_id, cluster_id)`; `centroids` is the
    `(k, dim)` matrix those ids index. The merge writes `cluster_id` in the same pass that
    writes the row, which is why this is an input to the plan and not an UPDATE afterwards:
    an UPDATE writes a second version of every row, and on 1.7M rows that is minutes and a
    table twice the size.
    """

    assignments: Path
    centroids: Any
    row_counts: tuple[int, ...]
    generation: int = 1


@dataclass(frozen=True)
class DeltaSpec:
    """A bundle that is a change to a loaded corpus, not a corpus (`db.delta`).

    The delta directory is bundle-shaped (the same shards, sidecars and checksummed
    inventory) plus a `scope.parquet` of `(kind, doc_id)`: `removed` documents leave the
    corpus, `status` and `relations` name documents whose derived rows are replaced.
    `parent_manifest_sha256` is the version the database must be at, checked inside the
    transaction, and `child_*` is the version it is at afterwards.
    """

    parent_manifest_sha256: str
    child_manifest_sha256: str
    child_source_manifest_sha256: str
    child_chunk_count: int
    added_docs: int = 0
    removed_docs: int = 0
    added_chunks: int = 0
    removed_chunks: int = 0
    run_id: str = ""


def _guarded(table: str, statement: str) -> str:
    """`statement`, run only when `table` exists: a corpus without that sidecar has none."""
    return (
        f"DO $$ BEGIN IF to_regclass({quote_literal(table)}) IS NOT NULL THEN "
        f"{statement} END IF; END $$;"
    )


def _delta_removals(corpus_id: str) -> str:
    c = quote_literal(corpus_id)
    removed = "SELECT doc_id FROM scope_stage WHERE kind = 'removed'"
    own_chunks = f"SELECT id FROM chunks WHERE corpus_id = {c} AND doc_id IN ({removed})"
    return "\n".join(
        [
            _guarded(
                "chunk_signals",
                f"DELETE FROM chunk_signals WHERE corpus_id = {c} "
                f"AND chunk_id IN ({own_chunks});",
            ),
            _guarded(
                "chunk_entities",
                f"DELETE FROM chunk_entities WHERE corpus_id = {c} "
                f"AND chunk_id IN ({own_chunks});",
            ),
            f"DELETE FROM chunks WHERE corpus_id = {c} AND doc_id IN ({removed});",
            _guarded(
                "doc_quality",
                f"DELETE FROM doc_quality WHERE corpus_id = {c} AND doc_id IN ({removed});",
            ),
            _guarded(
                "doc_status",
                f"DELETE FROM doc_status WHERE corpus_id = {c} AND doc_id IN "
                "(SELECT doc_id FROM scope_stage WHERE kind IN ('removed', 'status'));",
            ),
            _guarded(
                "doc_relations",
                f"DELETE FROM doc_relations WHERE corpus_id = {c} AND source_doc_id IN "
                "(SELECT doc_id FROM scope_stage WHERE kind IN ('removed', 'relations'));",
            ),
        ]
    )


def _delta_parent_check(corpus_id: str, parent: str) -> str:
    c, p = quote_literal(corpus_id), quote_literal(parent)
    return (
        "DO $$ DECLARE current_sha TEXT; BEGIN\n"
        "  SELECT manifest_sha256 INTO current_sha FROM corpus_meta "
        f"WHERE id = {c} FOR UPDATE;\n"
        f"  IF current_sha IS NULL THEN RAISE EXCEPTION\n"
        f"    'ravel: corpus % is not loaded; a delta changes a loaded corpus', {c};\n"
        "  END IF;\n"
        f"  IF current_sha <> {p} THEN RAISE EXCEPTION\n"
        f"    'ravel: the database is at version %, this delta is from %', current_sha, {p};\n"
        "  END IF;\nEND $$;"
    )


def _delta_advance(corpus_id: str, delta: DeltaSpec) -> str:
    c = quote_literal(corpus_id)
    run = f", run_id = {quote_literal(delta.run_id)}" if delta.run_id else ""
    return (
        "UPDATE corpus_meta SET "
        f"manifest_sha256 = {quote_literal(delta.child_manifest_sha256)}, "
        f"source_manifest_sha256 = {quote_literal(delta.child_source_manifest_sha256)}{run} "
        f"WHERE id = {c};\n"
        "CREATE TABLE IF NOT EXISTS corpus_versions (\n"
        "    corpus_id TEXT NOT NULL REFERENCES corpus_meta(id) ON DELETE CASCADE,\n"
        "    manifest_sha256 TEXT NOT NULL, parent_sha256 TEXT NOT NULL,\n"
        "    applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),\n"
        "    added_docs INTEGER, removed_docs INTEGER,\n"
        "    added_chunks BIGINT, removed_chunks BIGINT,\n"
        "    PRIMARY KEY (corpus_id, manifest_sha256, applied_at)\n);\n"
        "INSERT INTO corpus_versions (corpus_id, manifest_sha256, parent_sha256, added_docs, "
        "removed_docs, added_chunks, removed_chunks) VALUES "
        f"({c}, {quote_literal(delta.child_manifest_sha256)}, "
        f"{quote_literal(delta.parent_manifest_sha256)}, {delta.added_docs}, "
        f"{delta.removed_docs}, {delta.added_chunks}, {delta.removed_chunks});"
    )


def quote_ident(name: str) -> str:
    """Quote an SQL identifier.

    The names here come from a manifest, which comes from a YAML file in the registry —
    reviewed, in git, and still not a place to take an identifier from unquoted. The cost
    of quoting is nothing and the failure mode of not quoting is a corpus id that executes.
    """
    return '"' + name.replace('"', '""') + '"'


def quote_literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


@dataclass(frozen=True, slots=True)
class Step:
    """One reviewable unit of the load."""

    name: str
    sql: str
    inputs: tuple[str, ...] = ()
    """Bundle-relative files this step reads. Empty for pure SQL."""

    transactional: bool = True
    note: str = ""

    def describe(self) -> str:
        where = f"  [{len(self.inputs)} files]" if self.inputs else ""
        return f"{self.name}{where}"


RELATION_COLUMNS = (
    "source_doc_id",
    "relation",
    "target_text",
    "target_type",
    "target_number",
    "target_year",
    "target_doc_id",
    "resolution",
    "confidence",
    "partial",
    "mentions",
    "evidence_chunk_id",
    "quote",
)
"""The columns of `relations.parquet`, in the bundle's order. Checked against the writer's
schema in `tests/test_load.py`, so a column added on one side fails there and not in a
database."""

STATUS_COLUMNS = ("doc_id", "status", "revoked_by", "amended_by")

_RELATION_STAGE = (
    "CREATE UNLOGGED TABLE relations_stage (\n"
    "    source_doc_id TEXT, relation TEXT, target_text TEXT, target_type TEXT,\n"
    "    target_number TEXT, target_year INTEGER, target_doc_id TEXT, resolution TEXT,\n"
    "    confidence REAL, partial BOOLEAN, mentions INTEGER, evidence_chunk_id TEXT,\n"
    "    quote TEXT\n);\n"
    "CREATE UNLOGGED TABLE status_stage (\n"
    "    doc_id TEXT, status TEXT, revoked_by TEXT[], amended_by TEXT[]\n);"
)

QUALITY_COLUMNS = (
    "doc_id",
    "verdict",
    "indexable",
    "chunks",
    "damaged_chunks",
    "words",
    "unknown_share",
    "damaged_share",
    "replacement_per_1000",
    "reasons",
)
"""The columns of `doc_quality.parquet`, in the bundle's order (see `tests/test_load.py`)."""

_QUALITY_STAGE = (
    "CREATE UNLOGGED TABLE quality_stage (\n"
    "    doc_id TEXT, verdict TEXT, indexable BOOLEAN, chunks INTEGER,\n"
    "    damaged_chunks INTEGER, words INTEGER, unknown_share REAL, damaged_share REAL,\n"
    "    replacement_per_1000 REAL, reasons TEXT[]\n);"
)


def copy_from(table: str, columns: tuple[str, ...], source: str) -> str:
    """A `COPY ... FROM STDIN` statement naming its columns, and the file that feeds it.

    ! Always an explicit column list. `COPY chunks FROM ...` with no list binds by
    position, so adding a column to the schema silently shifts every value in every row
    one place — a corpus that loads without error and is wrong in every field after the
    insertion point.

    ! `FROM STDIN`, never `FROM '<path>'`. Postgres `COPY` reads text, CSV or its own
    binary format from a *server-side* file, and a bundle holds parquet — so a statement
    naming a `.parquet` path is not a slow load or a permissions problem, it is a syntax
    the server has no reader for. The executor streams each shard through `cursor.copy()`,
    converting rows as it goes, which is also what makes a bundle on object storage
    loadable by a database that cannot see the filesystem it came from.

    The source file is carried as a comment above the statement and in `Step.inputs`, so a
    printed plan still says which shard each `COPY` consumes.
    """
    names = ", ".join(quote_ident(c) for c in columns)
    return (
        f"-- from {source}\nCOPY {quote_ident(table)} ({names}) FROM STDIN WITH (FORMAT text);"
    )


def _merge(
    corpus_id: str,
    *,
    with_vectors: bool,
    with_omni: bool = False,
    with_sparse: bool = False,
    with_placement: bool = False,
) -> str:
    """The one pass that moves staged rows into `chunks`.

    Every staged column is carried across. A column that reaches the staging table and
    stops there is provenance the bundle paid to produce and the database never receives —
    and nothing downstream can tell the difference between "not captured" and "not
    loaded".
    """
    targets = [target for _, _, target in _STAGED]
    sources = [f"c.{quote_ident(source)}" for source, _, _ in _STAGED]
    if with_vectors:
        targets.append("dense")
        sources.append("v.embedding")
    if with_omni:
        targets.append("embedding_omni")
        sources.append("o.embedding")
    if with_sparse:
        targets.append("sparse")
        sources.append("sp.sparse")

    if with_placement:
        targets.append("cluster_id")
        sources.append("pl.cluster_id")
    into = ", ".join(quote_ident(c) for c in ["corpus_id", *targets])
    select = ", ".join([quote_literal(corpus_id), *sources])
    join = (
        "FROM chunks_stage c JOIN vectors_stage v ON v.chunk_id = c.id"
        if with_vectors
        else "FROM chunks_stage c"
    )
    if with_omni:
        # ! LEFT, not inner: only chunks that have an image have an omni vector, and a text
        # chunk with none is a chunk, not a missing row.
        join += "\nLEFT JOIN vectors_omni_stage o ON o.chunk_id = c.id"
    if with_sparse:
        # ! LEFT, like omni: a chunk with no sparse row is still a chunk. The bundle build
        # already refused a sparse set that does not cover the chunks exactly.
        join += "\nLEFT JOIN sparse_stage sp ON sp.chunk_id = c.id"
    if with_placement:
        # ! LEFT: a chunk that is not indexable is in no cluster, by design.
        join += "\nLEFT JOIN placement_stage pl ON pl.chunk_id = c.id"
    return f"INSERT INTO chunks ({into})\nSELECT {select}\n{join};"


def load_plan(
    document: dict[str, Any],
    root: Path | str,
    *,
    include_indexes: bool = True,
    placement: Placement | None = None,
    delta: DeltaSpec | None = None,
) -> tuple[Step, ...]:
    """The whole procedure, in order, for one sealed bundle.

    `document` is a sealed `manifest.json` as `bundle.writer.read_bundle` returns it.
    """
    root = Path(root)
    inventory = document.get("inventory", {})
    counts: dict[str, int] = inventory.get("counts", {})
    corpus_id = str(document["corpus_id"])
    chunk_count = int(document["chunk_count"])
    expected_total = delta.child_chunk_count if delta is not None else chunk_count

    def files(prefix: str) -> tuple[str, ...]:
        return tuple(sorted(n for n in counts if n.startswith(prefix)))

    chunk_files = files("chunks/")
    vector_files = files("vectors/")
    omni_files = files("vectors_omni/")
    omni = document.get("omni")
    if bool(omni) != bool(omni_files):
        raise ValueError(
            "the manifest declares an omni space and the bundle has no vectors_omni shards, "
            "or the reverse: a second space with no vectors (or vectors with no declared "
            "space) cannot be loaded"
        )
    sparse_files = files("sparse/")
    sparse = document.get("sparse")
    if bool(sparse) != bool(sparse_files) and not (delta is not None and not chunk_files):
        raise ValueError(
            "the manifest declares a sparse arm and the bundle has no sparse shards, or the "
            "reverse: a vocabulary with no vectors would leave the fallback arm answering "
            "from a column of NULLs"
        )
    signal_files = files("signals/")

    steps: list[Step] = [
        Step(
            name="schema",
            sql=(root / "schema.sql").read_text(encoding="utf-8")
            if (root / "schema.sql").exists()
            else "-- schema.sql absent from the bundle",
            inputs=("schema.sql",),
            note="Generated from the manifest; every dimension is a variable.",
        ),
        Step(
            name="stamp corpus_meta",
            sql=_corpus_meta_insert(document),
            note=(
                "! Before the rows, not after. `chunks.corpus_id` REFERENCES "
                "corpus_meta(id), so inserting a chunk against a recipe that is not "
                "there yet is a foreign key violation — the load fails on its first row. "
                "It also reads better: the recipe exists before anything claims to have "
                "been built by it."
            ),
        ),
        *_canary_steps(document, root),
        Step(
            name="stage",
            sql=(
                "CREATE UNLOGGED TABLE chunks_stage (\n"
                + ",\n".join(
                    f"    {quote_ident(column):<20} {sql_type}"
                    for column, sql_type, _ in _STAGED
                )
                + "\n);\n"
                "CREATE UNLOGGED TABLE vectors_stage (chunk_id TEXT, embedding "
                f"halfvec({int(document['dense']['dim'])}));"
                + (
                    "\nCREATE UNLOGGED TABLE vectors_omni_stage (chunk_id TEXT, embedding "
                    f"halfvec({int(omni['dim'])}));"
                    if omni
                    else ""
                )
                + (
                    "\nCREATE UNLOGGED TABLE sparse_stage (chunk_id TEXT, sparse "
                    f"sparsevec({int(sparse['dim'])}));"
                    if sparse
                    else ""
                )
                + (
                    "\nCREATE UNLOGGED TABLE placement_stage "
                    "(chunk_id TEXT, cluster_id INTEGER);"
                    if placement
                    else ""
                )
            ),
            note=(
                "! Declared from the bundle's columns, never `LIKE chunks`. `LIKE` copies "
                "NOT NULL constraints onto columns the COPY does not supply — `corpus_id` "
                "among them, which the merge assigns — so the first row of the load fails "
                "a constraint the bundle was never asked to satisfy. UNLOGGED because "
                "staging is rebuilt from the bundle on any failure, so WAL for it is work "
                "whose only product is a recovery nobody would use."
            ),
        ),
        Step(
            name="copy chunks",
            sql="\n".join(
                copy_from("chunks_stage", CHUNK_COLUMNS, str(root / name))
                for name in chunk_files
            )
            or "-- no chunk shards in this bundle",
            inputs=chunk_files,
        ),
    ]
    if delta is not None:
        steps = [
            Step(
                name=s.name,
                sql=s.sql + "\nCREATE UNLOGGED TABLE scope_stage (kind TEXT, doc_id TEXT);",
                inputs=s.inputs,
                note=s.note,
            )
            if s.name == "stage"
            else s
            for s in steps
            if s.name not in ("schema", "stamp corpus_meta", "stamp canary")
        ]
        steps.insert(
            0,
            Step(
                name="check parent",
                sql=_delta_parent_check(corpus_id, delta.parent_manifest_sha256),
                note=(
                    "! Inside the transaction and under a row lock: a delta applied to the "
                    "wrong version, or twice, or beside another delta, is refused rather "
                    "than producing a corpus that is neither version."
                ),
            ),
        )
        steps.append(
            Step(
                name="copy scope",
                sql=copy_from("scope_stage", ("kind", "doc_id"), str(root / "scope.parquet")),
                inputs=("scope.parquet",),
                note="Which documents leave, and whose derived rows are replaced.",
            )
        )

    if omni_files:
        steps.append(
            Step(
                name="copy vectors_omni",
                sql="\n".join(
                    copy_from("vectors_omni_stage", ("chunk_id", "embedding"), str(root / name))
                    for name in omni_files
                ),
                inputs=omni_files,
                note=(
                    "The second space (EMBEDDING.md §4): its own width, its own column. Not "
                    "every chunk has one."
                ),
            )
        )
    if sparse_files:
        steps.append(
            Step(
                name="copy sparse",
                sql="\n".join(
                    copy_from("sparse_stage", ("chunk_id", "sparse"), str(root / name))
                    for name in sparse_files
                ),
                inputs=sparse_files,
                note="The lexical arm's vectors, as sparsevec text (BM25 weights, 1-based).",
            )
        )
    if placement is not None:
        steps.append(
            Step(
                name="copy placement",
                sql=copy_from(
                    "placement_stage", ("chunk_id", "cluster_id"), str(placement.assignments)
                ),
                inputs=(str(placement.assignments),),
                note="Each indexable chunk's cluster, fitted before the load (Placement).",
            )
        )
    if vector_files:
        steps.append(
            Step(
                name="copy vectors",
                sql="\n".join(
                    copy_from("vectors_stage", ("chunk_id", "embedding"), str(root / name))
                    for name in vector_files
                ),
                inputs=vector_files,
            )
        )
        steps.append(
            Step(
                name="merge",
                sql=_merge(
                    corpus_id,
                    with_vectors=True,
                    with_omni=bool(omni),
                    with_sparse=bool(sparse),
                    with_placement=placement is not None,
                ),
                note=(
                    "One write per row. An UPDATE would write a second version of every "
                    "row and leave the first as a dead tuple — see the module docstring."
                ),
            )
        )
        steps.append(
            Step(
                name="verify join",
                sql=(
                    "DO $$ DECLARE missing BIGINT; BEGIN\n"
                    "  SELECT count(*) INTO missing FROM chunks_stage c\n"
                    "    LEFT JOIN vectors_stage v ON v.chunk_id = c.id\n"
                    "   WHERE v.chunk_id IS NULL;\n"
                    "  IF missing > 0 THEN RAISE EXCEPTION\n"
                    "    'ravel: % chunks have no vector; the join would drop them', missing;\n"
                    "  END IF;\nEND $$;"
                ),
                note=(
                    "! Inside the transaction. A chunk whose vector did not arrive joins "
                    "away silently, and the corpus is short by exactly the rows nobody "
                    "counted."
                ),
            )
        )
    else:
        steps.append(
            Step(
                name="merge",
                sql=_merge(corpus_id, with_vectors=False),
                note="Text-only bundle: no vectors were written.",
            )
        )

    if omni_files:
        steps.append(
            Step(
                name="verify omni",
                sql=(
                    "DO $$ DECLARE orphans BIGINT; BEGIN\n"
                    "  SELECT count(*) INTO orphans FROM vectors_omni_stage o\n"
                    "    LEFT JOIN chunks_stage c ON c.id = o.chunk_id\n"
                    "   WHERE c.id IS NULL;\n"
                    "  IF orphans > 0 THEN RAISE EXCEPTION\n"
                    "    'ravel: % omni vectors belong to no chunk', orphans;\n"
                    "  END IF;\nEND $$;"
                ),
                note="An image vector for a chunk that is not in the corpus is a mis-join.",
            )
        )
    if signal_files:
        steps.append(
            Step(
                name="copy signals",
                sql="\n".join(
                    copy_from("chunk_signals", ("chunk_id", "data"), str(root / name))
                    for name in signal_files
                ),
                inputs=signal_files,
                note="Disposable: droppable and rebuildable without touching chunks.",
            )
        )
    has_relations = "relations.parquet" in counts
    has_status = "doc_status.parquet" in counts
    if has_relations or has_status:
        steps.append(
            Step(
                name="stage relations",
                sql=_RELATION_STAGE,
                note=(
                    "Declared from the bundle's columns for the same reason as `chunks_stage`: "
                    "`corpus_id` is assigned by the merge, not supplied by the file."
                ),
            )
        )
    if has_relations:
        steps.append(
            Step(
                name="copy relations",
                sql=copy_from(
                    "relations_stage", RELATION_COLUMNS, str(root / "relations.parquet")
                ),
                inputs=("relations.parquet",),
                note="Disposable: droppable and rebuildable without touching chunks.",
            )
        )
        steps.append(
            Step(
                name="merge relations",
                sql=(
                    "INSERT INTO doc_relations (corpus_id, "
                    + ", ".join(quote_ident(c) for c in RELATION_COLUMNS)
                    + ")\nSELECT "
                    + ", ".join([quote_literal(corpus_id), *map(quote_ident, RELATION_COLUMNS)])
                    + "\nFROM relations_stage;"
                ),
            )
        )
    if has_status:
        steps.append(
            Step(
                name="copy doc_status",
                sql=copy_from("status_stage", STATUS_COLUMNS, str(root / "doc_status.parquet")),
                inputs=("doc_status.parquet",),
            )
        )
        steps.append(
            Step(
                name="merge doc_status",
                sql=(
                    "INSERT INTO doc_status (corpus_id, "
                    + ", ".join(quote_ident(c) for c in STATUS_COLUMNS)
                    + ")\nSELECT "
                    + ", ".join([quote_literal(corpus_id), *map(quote_ident, STATUS_COLUMNS)])
                    + "\nFROM status_stage;"
                ),
            )
        )
    has_quality = "doc_quality.parquet" in counts
    if has_quality:
        steps.append(Step(name="stage quality", sql=_QUALITY_STAGE))
        steps.append(
            Step(
                name="copy doc_quality",
                sql=copy_from(
                    "quality_stage", QUALITY_COLUMNS, str(root / "doc_quality.parquet")
                ),
                inputs=("doc_quality.parquet",),
                note="Disposable: droppable and rebuildable without touching chunks.",
            )
        )
        steps.append(
            Step(
                name="merge doc_quality",
                sql=(
                    "INSERT INTO doc_quality (corpus_id, "
                    + ", ".join(quote_ident(c) for c in QUALITY_COLUMNS)
                    + ")\nSELECT "
                    + ", ".join([quote_literal(corpus_id), *map(quote_ident, QUALITY_COLUMNS)])
                    + "\nFROM quality_stage;"
                ),
            )
        )
    if has_quality:
        steps.append(
            Step(
                name="apply quality",
                sql=(
                    "UPDATE chunks c SET indexable = FALSE\n"
                    "FROM doc_quality q\n"
                    f"WHERE c.corpus_id = {quote_literal(corpus_id)}\n"
                    "  AND q.corpus_id = c.corpus_id AND q.doc_id = c.doc_id\n"
                    "  AND NOT q.indexable"
                    + (
                        "\n  AND c.doc_id IN (SELECT doc_id FROM quality_stage);"
                        if delta is not None
                        else ";"
                    )
                ),
                note=(
                    "! The engine already searches `WHERE indexable`, so a document the gate "
                    "rejected leaves every arm without a change to the engine. A flag set at "
                    "load from a sidecar: the chunk bodies are as sealed, and re-running the "
                    "gate with other thresholds sets it again."
                ),
            )
        )
    if "entities.parquet" in counts:
        steps.append(
            Step(
                name="copy chunk_entities",
                sql="-- COPY chunk_entities FROM entities.parquet",
                inputs=("entities.parquet",),
            )
        )

    steps.append(
        Step(
            name="verify counts",
            sql=(
                "DO $$ DECLARE loaded BIGINT; BEGIN\n"
                "  SELECT count(*) INTO loaded FROM chunks WHERE corpus_id = "
                f"{quote_literal(corpus_id)};\n"
                f"  IF loaded <> {expected_total} THEN RAISE EXCEPTION\n"
                f"    'ravel: loaded % rows, manifest claims {expected_total}', loaded;\n"
                "  END IF;\nEND $$;"
            ),
            note=(
                "! Before the commit, so 'fail any check -> load nothing' is the literal "
                "behaviour rather than an instruction to the operator."
            ),
        )
    )
    if placement is not None and delta is None:
        steps.append(
            Step(
                name="clusters",
                sql=_clusters_sql(corpus_id, placement, int(document["dense"]["dim"])),
                note="The centroids the engine routes by, in the same transaction as the rows.",
            )
        )
    if delta is not None:
        removal = Step(
            name="remove",
            sql=_delta_removals(corpus_id),
            note="Deleted whole, never edited: provenance is immutable (the guard forbids it).",
        )
        at = next(i for i, step in enumerate(steps) if step.name == "merge")
        steps.insert(at, removal)
        steps.append(
            Step(
                name="recount clusters",
                sql=(
                    "UPDATE clusters k SET row_count = (SELECT count(*) FROM chunks c "
                    f"WHERE c.corpus_id = {quote_literal(corpus_id)} AND c.cluster_id = k.id) "
                    f"WHERE k.corpus_id = {quote_literal(corpus_id)};"
                ),
                note="The centroids stay (same generation); only their sizes change.",
            )
        )
        steps.append(
            Step(
                name="advance version",
                sql=_delta_advance(corpus_id, delta),
                note="The database is now at the child version, and the lineage says how.",
            )
        )
    steps.append(
        Step(
            name="drop staging",
            sql=(
                "DROP TABLE chunks_stage; DROP TABLE vectors_stage;"
                + (" DROP TABLE vectors_omni_stage;" if omni else "")
                + (" DROP TABLE sparse_stage;" if sparse else "")
                + (" DROP TABLE placement_stage;" if placement is not None else "")
                + (" DROP TABLE scope_stage;" if delta is not None else "")
                + (
                    " DROP TABLE relations_stage; DROP TABLE status_stage;"
                    if has_relations or has_status
                    else ""
                )
                + (" DROP TABLE quality_stage;" if has_quality else "")
            ),
        )
    )
    steps.append(
        Step(name="commit", sql="COMMIT;", note="Everything above is one transaction.")
    )

    if include_indexes and delta is None:
        steps.append(
            Step(
                name="indexes",
                sql=(root / "indexes.sql").read_text(encoding="utf-8")
                if (root / "indexes.sql").exists()
                else "-- see schema.sql; indexes are applied after the load",
                transactional=False,
                note=(
                    "After the data, never before: index maintenance during COPY turns a "
                    "20-minute load into a six-hour one (BUNDLE.md §6)."
                ),
            )
        )
    return tuple(steps)


def _clusters_sql(corpus_id: str, placement: Placement, dim: int) -> str:
    """The `clusters` table and one row per centroid. The width is the corpus's `dim`."""
    if placement.centroids.shape[1] != dim:
        raise ValueError(
            f"centroids are {placement.centroids.shape[1]}-dimensional, the corpus is {dim}"
        )
    rows = []
    for index, centroid in enumerate(placement.centroids.tolist()):
        vector = "[" + ",".join(f"{x:.6g}" for x in centroid) + "]"
        rows.append(
            f"({index}, {quote_literal(corpus_id)}, {quote_literal(vector)}::halfvec({dim}), "
            f"{int(placement.row_counts[index])}, {int(placement.generation)})"
        )
    return (
        "CREATE TABLE IF NOT EXISTS clusters (id INTEGER PRIMARY KEY, "
        "corpus_id TEXT NOT NULL REFERENCES corpus_meta(id) ON DELETE CASCADE, "
        f"centroid halfvec({dim}) NOT NULL, row_count BIGINT NOT NULL DEFAULT 0, "
        "generation INTEGER NOT NULL DEFAULT 1); "
        "INSERT INTO clusters (id, corpus_id, centroid, row_count, generation) VALUES "
        + ", ".join(rows)
        + ";"
    )


def _canary_steps(document: dict[str, Any], root: Path) -> tuple[Step, ...]:
    """The bundle's reference sentence and vector, recorded in the database it loads into.

    ! The corpus carries its own canary so that whatever serves it can check its embedder
    against the recipe that built it, with nothing but this row: embed `text` the way a
    query is embedded and the result must land on `dense`. It is stamped here, by the
    loader, because the loader is where the bundle becomes a database; a deploy that had to
    remember to do it afterwards is a deploy that sometimes does not. A bundle with no
    reference has no canary row, and the serving engine falls back to its older check.
    """
    import numpy as np

    text_path, vector_path = root / "reference.txt", root / "reference.npy"
    if not (text_path.exists() and vector_path.exists()):
        return ()
    vector = np.load(vector_path).astype(np.float32).ravel()
    literal = "[" + ",".join(f"{x:.9g}" for x in vector.tolist()) + "]"
    corpus_id = str(document["corpus_id"])
    text = text_path.read_text(encoding="utf-8").strip()
    return (
        Step(
            name="stamp canary",
            sql=(
                "CREATE TABLE IF NOT EXISTS corpus_canary (corpus_id TEXT PRIMARY KEY "
                "REFERENCES corpus_meta(id) ON DELETE CASCADE, text TEXT NOT NULL, "
                "dense halfvec NOT NULL);"
                f" INSERT INTO corpus_canary (corpus_id, text, dense) VALUES "
                f"({quote_literal(corpus_id)}, {quote_literal(text)}, "
                f"{quote_literal(literal)}::halfvec);"
            ),
            inputs=("reference.txt", "reference.npy"),
            note="The corpus's own check of the space; the vector declares its width.",
        ),
    )


def _corpus_meta_insert(document: dict[str, Any]) -> str:
    dense = document["dense"]
    sparse = document.get("sparse") or {}
    columns = {
        "id": document["corpus_id"],
        "run_id": document["run_id"],
        "dense_model": dense["model"],
        "dense_model_version": dense["model_version"],
        "dense_dim": dense["dim"],
        "dense_pooling": dense["pooling"],
        "dense_normalize": dense["normalize"],
        "dense_dtype": dense["dtype"],
        "dense_padding_side": dense["padding_side"],
        "dense_instruction_style": dense["instruction_style"],
        "dense_doc_instruction": dense.get("doc_instruction", ""),
        "dense_query_instruction": dense.get("query_instruction", ""),
        "sparse_scheme": sparse.get("scheme"),
        "sparse_dim": sparse.get("dim"),
        "sparse_k1": sparse.get("k1"),
        "sparse_b": sparse.get("b"),
        "sparse_vocab_sha256": sparse.get("vocab_sha256"),
        "sparse_fit_docs": sparse.get("fit_docs"),
        "chunker": document.get("chunker", ""),
        "chunker_version": document.get("chunker_version", ""),
        "profile_ref": document.get("profile_ref", ""),
        # ! `None` when the bundle predates this field, ✗ `{}`. An empty object is a
        # vocabulary that declares nothing, which the engine would treat as "this corpus
        # has no hierarchy" -- a claim the profile never made. NULL says "not recorded",
        # and the engine falls back to its built-in tables and reports that it did.
        "scoring_vocabulary": _json_or_null(document.get("scoring_vocabulary")),
        "text_search_config": document.get("text_search_config", "simple"),
        "source_manifest_sha256": document["source_manifest_sha256"],
        "manifest_sha256": document["manifest_sha256"],
        "notes": document.get("notes", ""),
    }
    if document.get("omni"):
        # ! Only when there is one. A text bundle must still load into a database whose
        # `corpus_meta` predates the column, and naming it would fail that insert.
        columns["omni"] = _json_or_null(document["omni"])
    names = ", ".join(quote_ident(k) for k in columns)
    values = ", ".join(quote_literal(v) for v in columns.values())
    return f"INSERT INTO corpus_meta ({names})\nVALUES ({values});"


def _json_or_null(value: Any) -> str | None:
    """A JSONB literal, or None for a bundle that carries no vocabulary.

    ! `quote_literal` renders None as SQL NULL, which is exactly the distinction wanted:
    "not recorded" and "recorded as empty" are different claims about a corpus.
    """
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True, slots=True)
class PlanSummary:
    """What a plan would do, without doing it."""

    steps: tuple[Step, ...] = field(default_factory=tuple)

    @property
    def files(self) -> int:
        return sum(len(s.inputs) for s in self.steps)

    def describe(self) -> str:
        return "\n".join(f"{i + 1:>2}. {s.describe()}" for i, s in enumerate(self.steps))
