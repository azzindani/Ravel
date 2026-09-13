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

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["Step", "load_plan", "copy_from", "quote_literal", "quote_ident"]

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
    ("source_title", "TEXT", "source_title"),
    ("source_url", "TEXT", "source_url"),
    ("source_sha256", "TEXT", "source_sha256"),
    ("locator_page", "INTEGER", "locator_page"),
    ("locator_section", "TEXT", "locator_section"),
    ("heading_path", "TEXT", "heading_path"),
    ("identifier", "TEXT", "identifier"),
    ("chunker", "TEXT", "chunker"),
    ("chunker_version", "TEXT", "chunker_version"),
    ("config_hash", "TEXT", "config_hash"),
    ("profile", "TEXT", "profile"),
)

CHUNK_COLUMNS: tuple[str, ...] = tuple(source for source, _, _ in _STAGED)


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


def _merge(corpus_id: str, *, with_vectors: bool) -> str:
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

    into = ", ".join(quote_ident(c) for c in ["corpus_id", *targets])
    select = ", ".join([quote_literal(corpus_id), *sources])
    join = (
        "FROM chunks_stage c JOIN vectors_stage v ON v.chunk_id = c.id;"
        if with_vectors
        else "FROM chunks_stage c;"
    )
    return f"INSERT INTO chunks ({into})\nSELECT {select}\n{join}"


def load_plan(
    document: dict[str, Any],
    root: Path | str,
    *,
    include_indexes: bool = True,
) -> tuple[Step, ...]:
    """The whole procedure, in order, for one sealed bundle.

    `document` is a sealed `manifest.json` as `bundle.writer.read_bundle` returns it.
    """
    root = Path(root)
    inventory = document.get("inventory", {})
    counts: dict[str, int] = inventory.get("counts", {})
    corpus_id = str(document["corpus_id"])
    chunk_count = int(document["chunk_count"])

    def files(prefix: str) -> tuple[str, ...]:
        return tuple(sorted(n for n in counts if n.startswith(prefix)))

    chunk_files = files("chunks/")
    vector_files = files("vectors/")
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
                sql=_merge(corpus_id, with_vectors=True),
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
    sidecars = (("chunk_edges", "edges.parquet"), ("chunk_entities", "entities.parquet"))
    for table, name in sidecars:
        if name in counts:
            steps.append(
                Step(name=f"copy {table}", sql=f"-- COPY {table} FROM {name}", inputs=(name,))
            )

    steps.append(
        Step(
            name="verify counts",
            sql=(
                "DO $$ DECLARE loaded BIGINT; BEGIN\n"
                "  SELECT count(*) INTO loaded FROM chunks WHERE corpus_id = "
                f"{quote_literal(corpus_id)};\n"
                f"  IF loaded <> {chunk_count} THEN RAISE EXCEPTION\n"
                f"    'ravel: loaded % rows, manifest claims {chunk_count}', loaded;\n"
                "  END IF;\nEND $$;"
            ),
            note=(
                "! Before the commit, so 'fail any check -> load nothing' is the literal "
                "behaviour rather than an instruction to the operator."
            ),
        )
    )
    steps.append(
        Step(
            name="drop staging",
            sql="DROP TABLE chunks_stage; DROP TABLE vectors_stage;",
        )
    )
    steps.append(
        Step(name="commit", sql="COMMIT;", note="Everything above is one transaction.")
    )

    if include_indexes:
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
        "text_search_config": document.get("text_search_config", "simple"),
        "source_manifest_sha256": document["source_manifest_sha256"],
        "manifest_sha256": document["manifest_sha256"],
        "notes": document.get("notes", ""),
    }
    names = ", ".join(quote_ident(k) for k in columns)
    values = ", ".join(quote_literal(v) for v in columns.values())
    return f"INSERT INTO corpus_meta ({names})\nVALUES ({values});"


@dataclass(frozen=True, slots=True)
class PlanSummary:
    """What a plan would do, without doing it."""

    steps: tuple[Step, ...] = field(default_factory=tuple)

    @property
    def files(self) -> int:
        return sum(len(s.inputs) for s in self.steps)

    def describe(self) -> str:
        return "\n".join(f"{i + 1:>2}. {s.describe()}" for i, s in enumerate(self.steps))
