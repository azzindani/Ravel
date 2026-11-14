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

import dataclasses
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import psycopg  # noqa: E402

from bundle import (  # noqa: E402
    BundleManifest,
    BundleWriter,
    SparseSpec,
    iter_vectors,
    read_bundle,
    write_doc_quality,
    write_doc_status,
    write_relations,
    write_vectors,
)
from chunk.models import Chunk  # noqa: E402
from chunk.store import write_chunks  # noqa: E402
from embed import spec_for  # noqa: E402
from embed.spec import Modality  # noqa: E402
from load import Step, load_plan, preflight  # noqa: E402
from load.execute import LoadError, execute  # noqa: E402
from spec import Registry

DIM = 16
OMNI_DIM = 24
CHUNKS = 3
CORPUS = "ci_fixture"


def build_bundle(root: Path, *, omni: bool = False) -> Path:
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
        # ! From the registry, ✗ a literal. This is the one path that proves the
        # vocabulary reaches `corpus_meta` at all, and a hand-written dict here would
        # let the profile and the bundle disagree without any test noticing.
        scoring_vocabulary=Registry.load().get("id_regulation").spec.scoring_vocabulary,
        text_search_config="indonesian",
        omni=(
            dataclasses.replace(
                spec_for(dim=OMNI_DIM, id="ci-omni"), modality=Modality.OMNI, model="ravel/omni"
            )
            if omni
            else None
        ),
    )
    writer = BundleWriter(root=root / ("v2" if omni else "v1"), manifest=manifest)

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
    # ! The manifest above declares a sparse arm, so the bundle must carry its vectors: a
    # vocabulary with no vectors is refused by the plan. These are `sparsevec` text, 1-based.
    pq.write_table(
        pa.table(
            {
                "chunk_id": [c.id for c in chunks],
                "sparse": [f"{{{i + 1}:0.5,{i + 10}:1.25}}/2048" for i in range(CHUNKS)],
            }
        ),
        str(writer.shard_path("sparse", 0)),
    )
    writer.record("sparse/part-00000.parquet", CHUNKS)
    if omni:
        # ! Two of the three chunks have an image: the second space is partial by nature.
        omni_matrix = np.random.default_rng(1).normal(size=(2, OMNI_DIM)).astype(np.float32)
        writer.record(
            "vectors_omni/part-00000.parquet",
            write_vectors(
                writer.shard_path("vectors_omni", 0), ["c0", "c2"], omni_matrix, dim=OMNI_DIM
            ),
        )
    # ! The sidecars. Two documents, one revoking the other, one edge to an instrument that
    # is not in the corpus: the three shapes a real relations file has.
    writer.record(
        "relations.parquet",
        write_relations(
            writer.root / "relations.parquet",
            [
                {
                    "source_doc_id": "d1",
                    "relation": "revokes",
                    "target_text": "Peraturan Bupati Nomor 3 Tahun 2015",
                    "target_type": "PERATURAN BUPATI",
                    "target_number": "3",
                    "target_year": 2015,
                    "target_doc_id": "d0",
                    "resolution": "resolved",
                    "confidence": 1.0,
                    "partial": False,
                    "mentions": 1,
                    "evidence_chunk_id": "c0",
                    "quote": "Peraturan Bupati Nomor 3 Tahun 2015 dicabut dan dinyatakan",
                },
                {
                    "source_doc_id": "d1",
                    "relation": "based_on",
                    "target_text": "Undang-Undang Nomor 23 Tahun 2014",
                    "target_type": "UNDANG-UNDANG",
                    "target_number": "23",
                    "target_year": 2014,
                    "target_doc_id": None,
                    "resolution": "unresolved",
                    "confidence": 0.0,
                    "partial": False,
                    "mentions": 2,
                    "evidence_chunk_id": "c1",
                    "quote": "Undang-Undang Nomor 23 Tahun 2014 tentang Pemerintahan Daerah;",
                },
            ],
        ),
    )
    writer.record(
        "doc_status.parquet",
        write_doc_status(
            writer.root / "doc_status.parquet",
            [
                {"doc_id": "d0", "status": "revoked", "revoked_by": ["d1"], "amended_by": []},
                {"doc_id": "d1", "status": "none_known", "revoked_by": [], "amended_by": []},
            ],
        ),
    )
    writer.record(
        "doc_quality.parquet",
        write_doc_quality(
            writer.root / "doc_quality.parquet",
            [
                {
                    "doc_id": "d0",
                    "verdict": "clean",
                    "indexable": True,
                    "chunks": 1,
                    "damaged_chunks": 0,
                    "words": 120,
                    "unknown_share": 0.01,
                    "damaged_share": 0.0,
                    "replacement_per_1000": 0.0,
                    "reasons": [],
                },
                {
                    "doc_id": "d1",
                    "verdict": "unusable",
                    "indexable": False,
                    "chunks": 1,
                    "damaged_chunks": 1,
                    "words": 80,
                    "unknown_share": 0.6,
                    "damaged_share": 1.0,
                    "replacement_per_1000": 0.0,
                    "reasons": ["100% of words sit in damaged chunks (1 of 1)"],
                },
            ],
        ),
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

            conn.execute(
                "DROP TABLE IF EXISTS chunks_stage, vectors_stage, sparse_stage, chunks CASCADE"
            )
            conn.execute(
                "DROP TABLE IF EXISTS relations_stage, status_stage, quality_stage, "
                "doc_relations, doc_status, doc_quality"
            )
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

            # 4b. the sidecars, through the same COPY the executor will use: an explicit
            #     column list, FROM STDIN, rows converted from parquet as they go.
            conn.execute(step_sql(plan, "stage relations"))
            for name in ("relations", "doc_status"):
                sql = step_sql(plan, f"copy {name}")
                statement = " ".join(
                    line for line in sql.splitlines() if not line.startswith("--")
                )
                rows = pq.read_table(root / f"{name}.parquet").to_pylist()
                with conn.cursor() as cur, cur.copy(statement) as copy:
                    for row in rows:
                        copy.write_row(tuple(row.values()))
            conn.execute(step_sql(plan, "merge relations"))
            conn.execute(step_sql(plan, "merge doc_status"))
            revoked_by = one(
                conn.execute(
                    "SELECT r.source_doc_id, s.status FROM doc_relations r "
                    "JOIN doc_status s ON s.doc_id = r.target_doc_id "
                    "AND s.corpus_id = r.corpus_id "
                    "WHERE r.relation = 'revokes' AND r.target_doc_id = 'd0'"
                ).fetchone(),
                "the revocation joins to the status it caused",
            )
            check(
                "relation sidecars load and answer 'what revoked this?'",
                tuple(revoked_by) == ("d1", "revoked"),
                f"{revoked_by[0]} -> {revoked_by[1]}",
            )
            dangling = one(
                conn.execute(
                    "SELECT count(*) FROM doc_relations WHERE target_doc_id IS NULL"
                ).fetchone(),
                "dangling edges counted",
            )
            check("an edge to an instrument outside the corpus is kept", dangling[0] == 1)

            conn.execute(step_sql(plan, "stage quality"))
            quality_sql = " ".join(
                line
                for line in step_sql(plan, "copy doc_quality").splitlines()
                if not line.startswith("--")
            )
            with conn.cursor() as cur, cur.copy(quality_sql) as copy:
                for row in pq.read_table(root / "doc_quality.parquet").to_pylist():
                    copy.write_row(tuple(row.values()))
            conn.execute(step_sql(plan, "merge doc_quality"))
            skipped = one(
                conn.execute(
                    "SELECT array_agg(doc_id), min(reasons[1]) FROM doc_quality "
                    "WHERE NOT indexable"
                ).fetchone(),
                "the unindexable documents are listed",
            )
            check(
                "quality sidecar loads and names the documents to leave out",
                list(skipped[0]) == ["d1"] and "damaged" in skipped[1],
                f"{skipped[0]}: {skipped[1]}",
            )

            # ! The step that carries the verdict to the engine, which already searches
            # `WHERE indexable`. Two throwaway chunks, removed again so the count guard
            # below still sees exactly the bundle's rows.
            conn.execute(
                "INSERT INTO chunks (id, corpus_id, body, source_title, source_url, doc_id) "
                "VALUES ('q0', %s, 'isi', 't', 'https://example/q', 'd0'), "
                "('q1', %s, 'isi', 't', 'https://example/q', 'd1')",
                (CORPUS, CORPUS),
            )
            conn.execute(step_sql(plan, "apply quality"))
            flags: dict[str, bool] = dict(
                conn.execute(
                    "SELECT id, indexable FROM chunks WHERE id IN ('q0', 'q1')"
                ).fetchall()
            )
            conn.execute("DELETE FROM chunks WHERE id IN ('q0', 'q1')")
            check(
                "an unindexable document's chunks leave the arms, the others stay",
                flags == {"q0": True, "q1": False},
                str(flags),
            )

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

        # 9. the executor, end to end: parquet shards streamed through COPY into a clean
        #    database, merged, verified, indexed. Everything above ran the plan's pieces by
        #    hand; this is the one path that moves real chunk rows and real vectors.
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(
                "DROP TABLE IF EXISTS chunks_stage, vectors_stage, sparse_stage, chunks CASCADE"
            )
            conn.execute(
                "DROP TABLE IF EXISTS relations_stage, status_stage, quality_stage, "
                "doc_relations, doc_status, doc_quality"
            )
            conn.execute("DROP TABLE IF EXISTS ingest_progress, corpus_meta CASCADE")

        loaded = execute(root, dsn)
        check(
            "the executor loads a bundle end to end",
            loaded.committed and loaded.rows.get("copy chunks") == CHUNKS,
            f"{len(loaded.steps)} steps, {loaded.rows}",
        )
        with psycopg.connect(dsn) as conn:
            counts = one(
                conn.execute(
                    "SELECT count(*), count(dense), "
                    "count(*) FILTER (WHERE NOT indexable) FROM chunks WHERE corpus_id = %s",
                    (CORPUS,),
                ).fetchone(),
                "chunks counted",
            )
            check(
                "every chunk arrived with its vector",
                counts[0] == CHUNKS and counts[1] == CHUNKS,
                f"{counts[0]} chunks, {counts[1]} with a dense vector",
            )
            stored = one(
                conn.execute(
                    "SELECT dense::real[] FROM chunks WHERE id = 'c0'",
                ).fetchone(),
                "a vector read back",
            )
            check(
                "a stored vector reads back as the one that was written",
                np.allclose(
                    np.asarray(stored[0], dtype=np.float32),
                    np.asarray(next(iter_vectors(root / "vectors/part-00000.parquet"))[1]),
                    atol=2e-3,
                ),
            )
            staged = one(
                conn.execute(
                    "SELECT count(*) FROM pg_tables WHERE tablename LIKE '%\\_stage'"
                ).fetchone(),
                "staging tables counted",
            )
            check("staging tables are dropped", staged[0] == 0)
            lexical = one(
                conn.execute(
                    "SELECT count(*), count(sparse), "
                    "(SELECT id FROM chunks "
                    "ORDER BY sparse <#> '{11:1}/2048'::sparsevec LIMIT 1) "
                    "FROM chunks"
                ).fetchone(),
                "the sparse column counted",
            )
            check(
                "the lexical arm's vectors reach the sparse column and rank by inner product",
                tuple(lexical) == (CHUNKS, CHUNKS, "c1"),
                str(tuple(lexical)),
            )

        refused = ""
        try:
            execute(root, dsn)
        except LoadError as exc:
            refused = str(exc)
        check(
            "loading the same corpus twice is refused, not overwritten",
            "already loaded" in refused,
            refused[:80],
        )
        again = execute(root, dsn, replace=True)
        with psycopg.connect(dsn) as conn:
            after = one(
                conn.execute(
                    "SELECT count(*) FROM chunks WHERE corpus_id = %s", (CORPUS,)
                ).fetchone(),
                "chunks recounted",
            )
        check(
            "replace swaps the corpus without duplicating it",
            again.replaced and after[0] == CHUNKS,
            f"{after[0]} chunks",
        )

        # 9b. the whole path a deployment takes: place, load with the placement, accelerate,
        #     verify. The database must say it is ready, and the unusable document's chunks
        #     must be in no cluster. The server has neither RUM nor pg_search, so the
        #     accelerators are reported skipped and the rest still has to hold.
        from db import prepare

        prepared = prepare(root, dsn, replace=True, rows_per_cluster=1)
        check(
            "prepare reports the database ready",
            prepared.ready.get("clusters", 0) >= 1
            and prepared.ready["placed"] == prepared.ready["servable"],
            str(prepared.ready),
        )
        with psycopg.connect(dsn) as conn:
            leftover = one(
                conn.execute(
                    "SELECT count(*) FILTER (WHERE NOT indexable AND cluster_id IS NOT NULL),"
                    " count(*) FILTER (WHERE indexable AND cluster_id IS NULL) FROM chunks"
                ).fetchone(),
                "placement counted",
            )
            canary = one(
                conn.execute("SELECT vector_dims(dense) FROM corpus_canary").fetchone(),
                "canary read",
            )
        check(
            "an unusable document's chunks are in no cluster and every other chunk is in one",
            tuple(leftover) == (0, 0),
            str(tuple(leftover)),
        )
        with psycopg.connect(dsn) as conn:
            from db.layout import layout_problems

            laid = one(
                conn.execute(
                    "SELECT count(*), bool_and(cluster_id IS NOT NULL) FROM chunk_vectors"
                ).fetchone(),
                "layout counted",
            )
            servable_rows = one(
                conn.execute(
                    "SELECT count(*) FROM chunks WHERE indexable AND cluster_id IS NOT NULL"
                ).fetchone(),
                "servable counted",
            )
            layout_issues = layout_problems(conn)
        check(
            "chunk_vectors holds every servable vector, in a cluster, stored PLAIN",
            laid[0] == servable_rows[0] > 0 and not layout_issues,
            f"{laid[0]} vs {servable_rows[0]} {layout_issues}",
        )
        check(
            "the corpus canary is recorded at the corpus width", canary[0] == DIM, str(canary)
        )
        refused_update = ""
        try:
            with psycopg.connect(dsn, autocommit=True) as conn:
                conn.execute("UPDATE chunks SET source_url = 'x' WHERE id = 'c0'")
        except psycopg.Error as exc:
            refused_update = str(exc)
        check(
            "the provenance guard refuses an UPDATE of provenance",
            "immutable" in refused_update,
            refused_update[:60],
        )

        # 10. the second vector space, through the same executor: its own column at its own
        #     width, a partial fill, and a cosine query that touches no text vector.
        omni_root = build_bundle(Path(tmp), omni=True)
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(
                "DROP TABLE IF EXISTS chunks_stage, vectors_stage, sparse_stage, chunks CASCADE"
            )
            conn.execute("DROP TABLE IF EXISTS vectors_omni_stage, chunk_vectors")
            conn.execute("DROP TABLE IF EXISTS clusters, corpus_canary")
            conn.execute(
                "DROP TABLE IF EXISTS relations_stage, status_stage, quality_stage, "
                "doc_relations, doc_status, doc_quality"
            )
            conn.execute("DROP TABLE IF EXISTS ingest_progress, corpus_meta CASCADE")
        loaded_omni = execute(omni_root, dsn)
        check(
            "a bundle with a second space loads",
            loaded_omni.committed and loaded_omni.rows.get("copy vectors_omni") == 2,
            str(loaded_omni.rows),
        )
        with psycopg.connect(dsn) as conn:
            fill = one(
                conn.execute(
                    "SELECT count(*), count(dense), count(embedding_omni) FROM chunks"
                ).fetchone(),
                "omni fill counted",
            )
            check(
                "the second column is partial and the text column is whole",
                tuple(fill) == (CHUNKS, CHUNKS, 2),
                f"{fill[0]} chunks, {fill[1]} dense, {fill[2]} omni",
            )
            width = one(
                conn.execute(
                    "SELECT atttypmod FROM pg_attribute "
                    "WHERE attrelid = 'chunks'::regclass AND attname = 'embedding_omni'"
                ).fetchone(),
                "the omni column exists",
            )
            check("the second column has its own width", width[0] == OMNI_DIM, str(width[0]))
            nearest = one(
                conn.execute(
                    "SELECT id FROM chunks WHERE embedding_omni IS NOT NULL "
                    "ORDER BY embedding_omni <=> (SELECT embedding_omni FROM chunks "
                    "WHERE id = 'c2') LIMIT 1"
                ).fetchone(),
                "an omni nearest neighbour",
            )
            check("a cosine query runs on the second space alone", nearest[0] == "c2")
            meta = one(
                conn.execute("SELECT omni->>'dim' FROM corpus_meta").fetchone(),
                "the omni block was stamped",
            )
            check("corpus_meta records the second space", meta[0] == str(OMNI_DIM), meta[0])
            indexed = one(
                conn.execute(
                    "SELECT count(*) FROM pg_indexes WHERE indexname = 'chunks_omni_idx'"
                ).fetchone(),
                "the omni index counted",
            )
            check("the omni index was built after the load", indexed[0] == 1)

        # 11. the update path: a delta from version 1 to version 2 must leave the database
        #     holding what a rebuild of version 2 would have loaded. Documents leave (b),
        #     arrive (d, e), one revokes an unchanged one (d revokes a, so a's status
        #     changes though a itself did not), and one arrives unusable (e).
        from synthetic_corpus import CORPUS as DELTA_CORPUS
        from synthetic_corpus import DocSpec, build_corpus

        from db import ApplyError, apply_delta, build_delta, prepare

        v1 = build_corpus(
            Path(tmp),
            {
                "a": DocSpec(2),
                "b": DocSpec(2),
                "c": DocSpec(1),
                "u": DocSpec(2, indexable=False),
            },
            name="delta-v1",
        )
        v2 = build_corpus(
            Path(tmp),
            {
                "a": DocSpec(2),
                "c": DocSpec(1),
                "d": DocSpec(3, revokes="a"),
                "e": DocSpec(1, indexable=False),
                "u": DocSpec(2, indexable=False),
            },
            name="delta-v2",
        )
        delta = build_delta(v1, v2, Path(tmp) / "delta-1-2")

        def reset() -> None:
            with psycopg.connect(dsn, autocommit=True) as conn:
                conn.execute(
                    "DROP TABLE IF EXISTS chunks_stage, vectors_stage, sparse_stage, "
                    "placement_stage, scope_stage, relations_stage, status_stage, "
                    "quality_stage, "
                    "doc_relations, doc_status, doc_quality, clusters, corpus_canary, "
                    "corpus_versions, chunk_vectors, chunks CASCADE"
                )
                conn.execute("DROP TABLE IF EXISTS ingest_progress, corpus_meta CASCADE")

        def state() -> dict[str, Any]:
            with psycopg.connect(dsn) as conn:
                q = conn.execute
                return {
                    "chunks": sorted(
                        q(
                            "SELECT id, doc_id, indexable, (cluster_id IS NOT NULL) "
                            "FROM chunks ORDER BY id"
                        ).fetchall()
                    ),
                    "status": sorted(
                        q("SELECT doc_id, status, revoked_by FROM doc_status").fetchall()
                    ),
                    "relations": sorted(
                        q(
                            "SELECT source_doc_id, relation, target_doc_id, resolution "
                            "FROM doc_relations"
                        ).fetchall()
                    ),
                    "quality": sorted(
                        q("SELECT doc_id, indexable FROM doc_quality").fetchall()
                    ),
                    "meta": q(
                        "SELECT manifest_sha256, source_manifest_sha256 FROM corpus_meta"
                    ).fetchone(),
                    "sizes": q("SELECT coalesce(sum(row_count), 0) FROM clusters").fetchone(),
                    "servable": q(
                        "SELECT count(*) FROM chunks WHERE indexable AND cluster_id IS NOT NULL"
                    ).fetchone(),
                    "layout": sorted(q("SELECT id FROM chunk_vectors").fetchall()),
                }

        reset()
        prepare(v1, dsn, replace=True, rows_per_cluster=1)
        applied = apply_delta(delta.out, dsn)
        after_delta = state()
        with psycopg.connect(dsn) as conn:
            versions = one(
                conn.execute(
                    "SELECT count(*), max(parent_sha256), max(manifest_sha256) "
                    "FROM corpus_versions"
                ).fetchone(),
                "lineage read",
            )

        reset()
        prepare(v2, dsn, replace=True, rows_per_cluster=1)
        rebuilt = state()
        check(
            "applying a delta leaves the chunks a rebuild would have",
            [c[:3] for c in after_delta["chunks"]] == [c[:3] for c in rebuilt["chunks"]],
            f"{len(after_delta['chunks'])} vs {len(rebuilt['chunks'])} chunks",
        )
        check(
            "the derived rows match a rebuild, including an unchanged document's new status",
            after_delta["status"] == rebuilt["status"]
            and after_delta["relations"] == rebuilt["relations"]
            and after_delta["quality"] == rebuilt["quality"],
            f"{after_delta['status']}",
        )
        check(
            "after a delta, chunk_vectors holds what a rebuild's would",
            after_delta["layout"] == rebuilt["layout"] and len(rebuilt["layout"]) > 0,
            f"{len(after_delta['layout'])} vs {len(rebuilt['layout'])} vectors",
        )
        check(
            "every servable chunk is in a cluster and the cluster sizes add up",
            all(c[3] == c[2] for c in after_delta["chunks"])
            and after_delta["sizes"][0] == after_delta["servable"][0] > 0,
            f"{after_delta['sizes']} vs {after_delta['servable']}",
        )
        check(
            "the database is at the child version, and the lineage says from where",
            after_delta["meta"][0] == read_bundle(v2)["manifest_sha256"]
            and tuple(versions)
            == (1, read_bundle(v1)["manifest_sha256"], read_bundle(v2)["manifest_sha256"]),
            str(tuple(versions)),
        )
        check(
            "the delta reported what it changed",
            (applied.added_docs, applied.removed_docs) == (2, 1),
        )

        stale = ""
        try:
            apply_delta(
                delta.out, dsn
            )  # the database was rebuilt at v2: not the delta's parent
        except ApplyError as exc:
            stale = str(exc)
        check(
            "a delta is refused by a database that is not at its parent version",
            "version" in stale,
            stale[:80],
        )
        reset()
        prepare(v1, dsn, replace=True, rows_per_cluster=1)
        apply_delta(delta.out, dsn)
        twice = ""
        try:
            apply_delta(delta.out, dsn)
        except ApplyError as exc:
            twice = str(exc)
        check("applying the same delta twice is refused", "version" in twice, twice[:80])
        del DELTA_CORPUS

    print("\nall bundle SQL executed against a real server")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
