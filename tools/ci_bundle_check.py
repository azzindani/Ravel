"""Execute a bundle's generated SQL against a real Postgres.

Ravel generates DDL, a load plan and two guard blocks, and until this script existed
**none of it had ever been run by a database**. Every test in `tests/test_load.py` asserts
things about strings. That is worth doing and it is not the same as knowing the SQL is
valid — Vera learned the equivalent lesson the hard way, and its CI comment says it
plainly: without a job on the real server, "the SQL that actually serves queries is never
executed by the gate."

Writing this found two defects that no string test could have:

1. `COPY ... FROM '<path>.parquet'` — Postgres `COPY` reads text, CSV or its own binary
   format from a server-side file. Parquet is not one of them, so every shard would have
   failed at the first statement. Now `FROM STDIN`, streamed by the executor.
2. `chunks.corpus_id REFERENCES corpus_meta(id)` with `stamp corpus_meta` scheduled
   *after* the merge, following `BUNDLE.md` §6's reading order. The first inserted row
   would have violated the foreign key.

What this proves, in order
--------------------------
- the generated schema applies to a real pgvector server at the manifest's dimensions;
- `indexes.sql` applies afterwards, and applies twice (it claims `IF NOT EXISTS`);
- the `corpus_meta` stamp inserts, with every column the manifest is stricter about;
- **the count guard actually fires** when the row count is wrong;
- **and stops firing** when it is right — a guard that always raises is as useless as one
  that never does, and only running it in one direction proves nothing (`CLAUDE.md` §15).

Usage:  DATABASE_URL="host=... dbname=..." python tools/ci_bundle_check.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import psycopg  # noqa: E402

from bundle import (  # noqa: E402
    BundleManifest,
    BundleWriter,
    SparseSpec,
    read_bundle,
    write_vectors,
)
from chunk.models import Chunk  # noqa: E402
from chunk.store import write_chunks  # noqa: E402
from embed import spec_for  # noqa: E402
from load import Step, load_plan, preflight  # noqa: E402

DIM = 16
CHUNKS = 3
CORPUS = "ci_fixture"


def build_bundle(root: Path) -> Path:
    """A complete, sealed bundle — small, and shaped like the real one.

    `text_search_config='indonesian'` on purpose: it is the setting `ABSORPTION.md` §8.3
    argues for, and whether the server actually has that dictionary is a fact about the
    server, not about this repository. A missing one fails here rather than on the box.
    """
    manifest = BundleManifest(
        corpus_id=CORPUS,
        run_id="ci",
        chunk_count=CHUNKS,
        source_manifest_sha256="a" * 64,
        dense=spec_for(dim=DIM, id="ci-hash"),
        sparse=SparseSpec(
            scheme="bm25", dim=2048, k1=1.5, b=0.75, vocab_sha256="b" * 64, fit_docs=CHUNKS
        ),
        chunker="unit",
        chunker_version="1.0",
        profile_ref="id_regulation@1.0",
        text_search_config="indonesian",
    )
    writer = BundleWriter(root=root / "v1", manifest=manifest)

    chunks = [
        Chunk(
            id=f"c{i}",
            doc_id="d0",
            body=f"Pasal {i + 1} memuat ketentuan umum yang wajib ditaati.",
            token_count=8,
            source_title="Peraturan Bupati Nomor 5 Tahun 2020",
            source_url="https://peraturan.go.id/id/perbup-5-2020",
            source_sha256="b" * 64,
            identifier="Perbup 5/2020",
            chunker="unit",
            chunker_version="1.0",
            config_hash="h",
        )
        for i in range(CHUNKS)
    ]
    writer.record(
        "chunks/part-00000.parquet",
        write_chunks(chunks, str(writer.shard_path("chunks", 0))),
    )

    matrix = np.random.default_rng(0).normal(size=(CHUNKS, DIM)).astype(np.float32)
    writer.record(
        "vectors/part-00000.parquet",
        write_vectors(writer.shard_path("vectors", 0), [c.id for c in chunks], matrix, dim=DIM),
    )
    writer.write_reference(matrix[0], "Pasal 1")
    writer.write_failed([])
    writer.seal(clustering={"algo": "kmeans/cosine", "k": 2, "generation": 1})
    return writer.root


def step_sql(plan: Sequence[Step], name: str) -> str:
    return next(step for step in plan if step.name == name).sql


def one(row: tuple[Any, ...] | None, what: str) -> tuple[Any, ...]:
    """A query that returned nothing when it must return something.

    ! Not `row[0]` against an Optional. A missing row here means the statement
    above did not do what this script claims it did, and an `IndexError` three
    lines later names the wrong thing.
    """
    if row is None:
        print(f"FAIL {what}: the query returned no rows", file=sys.stderr)
        sys.exit(1)
    return row


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"{'ok  ' if condition else 'FAIL'} {label}{f': {detail}' if detail else ''}")
    if not condition:
        sys.exit(1)


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        root = build_bundle(Path(tmp))
        document = read_bundle(root)
        plan = load_plan(document, root)
        recipe = document["manifest_sha256"][:12]
        print(f"bundle {root}  ·  {len(plan)} steps  ·  recipe {recipe}")

        with psycopg.connect(dsn, autocommit=True) as conn:
            # 0. ! Prove pgvector is here before anything depends on it. Without this the
            #    schema step would fail on a missing `halfvec` type and the job would be
            #    reporting on error handling rather than on the schema — the same shape as
            #    a preflight that goes green because none of its inputs were available.
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            version = conn.execute(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            ).fetchone()
            check(
                "pgvector is installed",
                version is not None,
                str(version[0]) if version else "",
            )

            conn.execute("DROP TABLE IF EXISTS chunks_stage, vectors_stage, chunks CASCADE")
            conn.execute("DROP TABLE IF EXISTS ingest_progress, corpus_meta CASCADE")

            # 1. the generated schema, at the manifest's dimensions.
            conn.execute(step_sql(plan, "schema"))
            width = one(
                conn.execute(
                    "SELECT atttypmod FROM pg_attribute "
                    "WHERE attrelid = 'chunks'::regclass AND attname = 'dense'"
                ).fetchone(),
                "the dense column exists",
            )
            check("schema applies", True, f"dense column typmod {width[0]}")

            config = conn.execute(
                "SELECT cfgname FROM pg_ts_config WHERE cfgname = %s",
                (document["text_search_config"],),
            ).fetchone()
            check(
                "the declared text search config exists",
                config is not None,
                document["text_search_config"],
            )

            # 2. indexes, from the bundle's own file, applied twice — it claims
            #    IF NOT EXISTS, and a second apply is what a resumed load would do.
            indexes = (root / "indexes.sql").read_text(encoding="utf-8")
            conn.execute(indexes)
            conn.execute(indexes)
            check("indexes apply, and re-apply", True)

            # 3. the recipe row, with every column the manifest is stricter about.
            conn.execute(step_sql(plan, "stamp corpus_meta"))
            stamped = one(
                conn.execute(
                    "SELECT manifest_sha256, dense_padding_side, "
                    "dense_instruction_style, sparse_fit_docs "
                    "FROM corpus_meta WHERE id = %s",
                    (CORPUS,),
                ).fetchone(),
                "the corpus_meta row was stamped",
            )
            check(
                "corpus_meta carries the recipe",
                stamped[0] == document["manifest_sha256"],
                f"padding_side={stamped[1]} style={stamped[2]} sparse_fit_docs={stamped[3]}",
            )

            # 4. staging tables at the manifest's width.
            conn.execute(step_sql(plan, "stage"))
            check("staging tables create", True)

        # 5. the count guard must fire on a short load...
        with psycopg.connect(dsn) as conn:
            fired = False
            try:
                conn.execute(step_sql(plan, "verify counts"))
            except psycopg.errors.RaiseException as exc:
                fired = "loaded 0 rows" in str(exc)
            conn.rollback()
            check("the count guard fires when rows are missing", fired)

        # 6. ...and must NOT fire when the load is complete. A guard that always raises
        #    is as useless as one that never does.
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "INSERT INTO chunks (id, corpus_id, body, source_title, source_url) "
                "SELECT 'x' || g, %s, 'isi', 't', 'https://example/x' "
                "FROM generate_series(1, %s) g",
                (CORPUS, CHUNKS),
            )
            passed = True
            try:
                conn.execute(step_sql(plan, "verify counts"))
            except psycopg.errors.RaiseException as exc:  # pragma: no cover - CI only
                passed = False
                print(exc, file=sys.stderr)
            conn.rollback()
            check("the count guard passes on a complete load", passed)

        # 7. the generated tsvector is actually populated by the server.
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "INSERT INTO chunks (id, corpus_id, body, source_title, source_url) "
                "VALUES ('t1', %s, 'Setiap orang wajib menaati ketentuan peraturan ini', "
                "'t', 'https://example/t')",
                (CORPUS,),
            )
            hit = one(
                conn.execute(
                    "SELECT tsv @@ plainto_tsquery(%s, 'menaati ketentuan') "
                    "FROM chunks WHERE id = 't1'",
                    (document["text_search_config"],),
                ).fetchone(),
                "the test chunk was inserted",
            )
            conn.rollback()
            check("the generated tsvector column indexes Indonesian text", bool(hit[0]))

        # 8. and the bundle still passes its own preflight.
        report = preflight(root, target_dim=DIM, free_bytes=10**12)
        check(
            "the bundle passes load preflight",
            not report.failed,
            f"{len(report.skipped)} checks skipped (no live embedder in CI)",
        )

    print("\nall bundle SQL executed against a real server")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
