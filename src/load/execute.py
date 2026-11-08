"""Running a load plan against a database: the executor `plan.py` was written for.

`plan.py` produces the procedure as data and nothing here changes it. This module walks it:
one transaction from the schema to the count check, `COMMIT` as an actual commit, then the
indexes outside the transaction (`plan.py`, module docstring).

What the executor adds, beyond running statements, is everything the statements cannot say
about themselves:

* **A `COPY` is fed from parquet, not from a file.** Postgres has no parquet reader, so each
  shard is streamed through `cursor.copy()` and converted row by row. A dense vector is
  written as the `halfvec` text form, five significant digits, which round-trips every
  half-precision value exactly: the shard stores fp16, so more digits would only be noise
  that makes the `COPY` payload larger.
* **It never overwrites a corpus quietly.** A `corpus_meta` row with this id means this
  corpus is already loaded; that is an error unless `replace` says to remove it, and removal
  happens inside the same transaction, so a load that fails leaves the old corpus as it was.
* **A failure is the step's name.** The database error says which constraint broke, not which
  part of an hour-long procedure was running; the executor attaches that.
* **Nothing of another corpus is touched.** Every delete is `WHERE corpus_id = ...`.

`psycopg` is the optional `load` extra and is imported when a load runs, never when this
module is imported.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

from bundle.writer import read_bundle
from load.plan import Step, load_plan, quote_ident

__all__ = ["ExecReport", "LoadError", "execute", "halfvec_text"]

#: Rows converted per `write_row` burst, and the grain progress is reported at.
BATCH_ROWS = 4096

#: Tables a replace clears, children before the `corpus_meta` row they reference. A table
#: the schema did not create (signals, entities) is skipped rather than assumed.
_CORPUS_TABLES = (
    "chunk_signals",
    "chunk_entities",
    "doc_relations",
    "doc_status",
    "doc_quality",
    "chunks",
)


class LoadError(RuntimeError):
    """The load failed, and nothing was committed. `step` says where."""

    def __init__(self, step: str, cause: BaseException) -> None:
        super().__init__(f"load failed at step {step!r}: {cause}")
        self.step = step
        self.cause = cause


@dataclass
class ExecReport:
    corpus_id: str
    committed: bool = False
    replaced: bool = False
    steps: list[tuple[str, float]] = field(default_factory=list)
    rows: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0

    def slowest(self) -> tuple[str, float] | None:
        return max(self.steps, key=lambda s: s[1], default=None)


def halfvec_text(vector: np.ndarray[Any, Any]) -> str:
    """`[0.1,0.2,...]`: what pgvector parses a `halfvec` from.

    `%.5g`: a half-precision float carries about three and a half digits, so five identify
    every representable value uniquely and nothing is lost.
    """
    return (
        "[" + ",".join(f"{x:.5g}" for x in np.asarray(vector, dtype=np.float32).tolist()) + "]"
    )


_COPY_TARGET = re.compile(r"COPY\s+(\"[^\"]+\")\s*\((.*?)\)\s+FROM STDIN", re.S)


def _statements(step: Step) -> list[tuple[str, str]]:
    """`(file, COPY statement)` pairs of a `copy` step, in the order the plan lists them."""
    statements = [
        line for line in step.sql.splitlines() if line.strip() and not line.startswith("--")
    ]
    if len(statements) != len(step.inputs):
        raise ValueError(
            f"{step.name}: {len(step.inputs)} input files and {len(statements)} COPY "
            "statements; the plan and its inputs disagree"
        )
    return list(zip(step.inputs, statements, strict=True))


def _columns(statement: str) -> tuple[str, ...]:
    found = _COPY_TARGET.search(statement)
    if found is None:
        raise ValueError(f"not a COPY ... FROM STDIN statement: {statement[:80]!r}")
    return tuple(c.strip().strip('"') for c in found.group(2).split(","))


def _rows(path: Path, columns: tuple[str, ...]) -> Iterator[list[tuple[Any, ...]]]:
    """Batches of row tuples in the COPY's column order. Vectors become `halfvec` text."""
    file = pq.ParquetFile(str(path))
    for batch in file.iter_batches(batch_size=BATCH_ROWS, columns=list(columns)):
        data = {name: batch.column(name).to_pylist() for name in columns}
        if "embedding" in data:
            data["embedding"] = [halfvec_text(np.asarray(v)) for v in data["embedding"]]
        yield list(zip(*(data[name] for name in columns), strict=True))


def _copy(
    conn: Any,
    root: Path,
    step: Step,
    on_rows: Callable[[str, int], None] | None,
) -> int:
    total = 0
    for name, statement in _statements(step):
        columns = _columns(statement)
        with conn.cursor() as cur, cur.copy(statement) as copy:
            for rows in _rows(root / name, columns):
                for row in rows:
                    copy.write_row(row)
                total += len(rows)
                if on_rows is not None:
                    on_rows(step.name, total)
    return total


def width_mismatch(existing: int | None, wanted: int) -> str | None:
    """Why a bundle cannot go into the `chunks` table that is already there, or None."""
    if existing is None or existing == wanted:
        return None
    return (
        f"the target database already has a chunks.dense column of {existing} dimensions and "
        f"this bundle is {wanted}-dimensional. Load into an empty database: tables from an "
        "earlier load are kept as they are, and a different width cannot be merged into them."
    )


def _existing_width(conn: Any) -> int | None:
    """Width of `chunks.dense` as the database has it (`halfvec(N)` stores N as typmod)."""
    row = conn.execute(
        "SELECT atttypmod FROM pg_attribute "
        "WHERE attrelid = to_regclass('chunks') AND attname = 'dense' AND NOT attisdropped"
    ).fetchone()
    if row is None or row[0] is None or int(row[0]) <= 0:
        return None
    return int(row[0])


def _clear(conn: Any, corpus_id: str) -> None:
    """Remove one corpus from the tables that hold it, and nothing of any other."""
    for table in _CORPUS_TABLES:
        exists = conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0]
        if exists is not None:
            conn.execute(f"DELETE FROM {quote_ident(table)} WHERE corpus_id = %s", (corpus_id,))
    conn.execute("DELETE FROM corpus_meta WHERE id = %s", (corpus_id,))


def execute(
    root: Path | str,
    dsn: str,
    *,
    replace: bool = False,
    include_indexes: bool = True,
    on_step: Callable[[str, str], None] | None = None,
    on_rows: Callable[[str, int], None] | None = None,
) -> ExecReport:
    """Load one sealed bundle. All or nothing up to the commit; indexes after.

    `on_step(name, "start" | "done")` and `on_rows(step, rows_so_far)` are how a caller shows
    progress through a procedure that is hours long at corpus scale.
    """
    import psycopg

    root = Path(root)
    document = read_bundle(root)
    corpus_id = str(document["corpus_id"])
    plan = load_plan(document, root, include_indexes=include_indexes)
    report = ExecReport(corpus_id=corpus_id)
    started = time.time()
    current = "connect"

    with psycopg.connect(dsn) as conn:
        try:
            current = "extension"
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")

            for step in plan:
                current = step.name
                if on_step:
                    on_step(step.name, "start")
                t0 = time.time()

                if step.name == "schema":
                    conn.execute(step.sql)
                    # ! Before a single row is staged. `CREATE TABLE IF NOT EXISTS` leaves a
                    # table from an earlier load exactly as it was, and a bundle of another
                    # width then fails at the final merge: 22 minutes of copying in, with
                    # nothing committed. The width is known the moment the schema ran.
                    mismatch = width_mismatch(
                        _existing_width(conn), int(document["dense"]["dim"])
                    )
                    if mismatch:
                        raise RuntimeError(mismatch)
                    present = conn.execute(
                        "SELECT 1 FROM corpus_meta WHERE id = %s", (corpus_id,)
                    ).fetchone()
                    if present is not None:
                        if not replace:
                            raise RuntimeError(
                                f"corpus {corpus_id!r} is already loaded; pass replace to "
                                "remove it and load this bundle in its place"
                            )
                        _clear(conn, corpus_id)
                        report.replaced = True
                elif step.name == "commit":
                    conn.commit()
                    report.committed = True
                elif step.name.startswith("copy ") and step.inputs:
                    report.rows[step.name] = _copy(conn, root, step, on_rows)
                elif not step.transactional:
                    conn.commit()
                    conn.autocommit = True
                    conn.execute(step.sql)
                else:
                    conn.execute(step.sql)

                report.steps.append((step.name, time.time() - t0))
                if on_step:
                    on_step(step.name, "done")
        except BaseException as exc:
            if not conn.autocommit:
                conn.rollback()
            raise LoadError(current, exc) from exc

    report.seconds = time.time() - started
    return report
