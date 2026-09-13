"""The load gate: seven checks, and a plan that can be read before it runs.

    Fail any check → load nothing. Partial loads are the worst outcome, because the
    resulting corpus looks fine.  — `BUNDLE.md` §5

Every check below is tested in the direction that matters: **capable of failing**. A gate
that only ever goes green is the defect `embed/preflight.py` shipped with on its first
day, when its own test passed the same embedder as both build and query side and reported
three green checks against cosine 1.000000 (`CLAUDE.md` §15).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from bundle import BundleLayoutError, BundleManifest, BundleWriter, read_bundle, write_vectors
from chunk.models import Chunk
from chunk.store import write_chunks
from embed import spec_for
from load import Status, copy_from, load_plan, preflight, quote_literal

DIM = 8


def _chunks(n: int) -> list[Chunk]:
    return [
        Chunk(
            id=f"c{i}",
            doc_id="d0",
            body=f"Pasal {i + 1} memuat ketentuan umum.",
            token_count=5,
            source_title="Peraturan Bupati Nomor 5 Tahun 2020",
            source_url="https://peraturan.go.id/id/perbup-5-2020",
            source_sha256="b" * 64,
            chunker="unit",
            chunker_version="1.0",
            config_hash="h",
        )
        for i in range(n)
    ]


@pytest.fixture
def built(tmp_path: Path) -> tuple[Path, np.ndarray]:
    """A small, complete, sealed bundle — the thing every check is run against."""
    manifest = BundleManifest(
        corpus_id="id_legal",
        run_id="r1",
        chunk_count=3,
        source_manifest_sha256="a" * 64,
        dense=spec_for(dim=DIM, id="q"),
        chunker="unit",
        chunker_version="1.0",
        text_search_config="indonesian",
    )
    writer = BundleWriter(root=tmp_path / "v1", manifest=manifest)
    rows = write_chunks(_chunks(3), str(writer.shard_path("chunks", 0)))
    writer.record("chunks/part-00000.parquet", rows)

    matrix = np.random.default_rng(0).normal(size=(3, DIM)).astype(np.float32)
    rows = write_vectors(
        writer.shard_path("vectors", 0), ["c0", "c1", "c2"], matrix, dim=DIM
    )
    writer.record("vectors/part-00000.parquet", rows)
    writer.write_reference(matrix[0], "Pasal 1")
    writer.write_failed([])
    writer.seal(clustering={"algo": "kmeans/cosine", "k": 2, "generation": 1})
    return writer.root, matrix


def green(root: Path, vector: np.ndarray, **over: object):
    kwargs: dict[str, object] = {
        "target_dim": DIM,
        "deployment": spec_for(dim=DIM, id="q"),
        "query_vector": vector.tolist(),
        "free_bytes": 10**12,
    }
    return preflight(root, **(kwargs | over))  # type: ignore[arg-type]


def named(report, name: str):
    return next(c for c in report.checks if c.name == name)


# -- the happy path, and the gate's own honesty ------------------------------------------


def test_a_complete_bundle_passes_every_check(built) -> None:
    root, matrix = built

    report = green(root, matrix[0])

    assert report.passed(), report.describe()
    assert not report.skipped


def test_a_check_that_could_not_run_is_skipped_not_passed(built) -> None:
    """! The rule most likely to be eroded in a hurry. A preflight that goes green on a
    laptop with none of its inputs available is worse than no preflight — it is one
    somebody trusts."""
    root, _ = built

    report = green(root, np.zeros(DIM), query_vector=None, free_bytes=None)

    assert {c.name for c in report.skipped} == {"canary", "disk"}
    assert not report.passed()
    assert report.passed(allow_skipped=True)


def test_a_failing_report_refuses_to_load_and_says_so(built) -> None:
    root, _ = built

    report = green(root, np.zeros(DIM), target_dim=1024)

    with pytest.raises(BundleLayoutError, match="nothing was loaded"):
        report.raise_for_status()


def test_a_directory_without_a_manifest_is_not_a_bundle(tmp_path: Path) -> None:
    report = preflight(tmp_path)

    assert not report.passed()
    assert "unsealed build directory" in named(report, "manifest").detail


# -- 1. dimension · 2. consistency · 3. canary ---------------------------------------------


def test_a_width_mismatch_names_both_widths(built) -> None:
    root, matrix = built

    check = named(green(root, matrix[0], target_dim=1024), "dimension")

    assert check.status is Status.FAIL
    assert "8-dimensional" in check.detail and "1024" in check.detail


def test_consistency_names_the_field_that_moved(built) -> None:
    """! Field by field, not by fingerprint. "Manifest mismatch" sends someone to diff two
    JSON blobs; "padding_side: bundle=left deployment=right" is the answer."""
    from embed import PaddingSide

    root, matrix = built
    drifted = spec_for(dim=DIM, id="q", padding_side=PaddingSide.RIGHT)

    check = named(green(root, matrix[0], deployment=drifted), "consistency")

    assert check.status is Status.FAIL
    assert "padding_side" in check.detail


def test_the_canary_fails_when_the_query_embedder_is_a_different_model(built) -> None:
    """! The one check that tests the live system rather than a recorded claim, and the
    one that catches what a manifest cannot describe: a quantised checkpoint, a different
    pooling implementation, a tokenizer that loaded with the other padding side."""
    root, matrix = built
    elsewhere = np.random.default_rng(99).normal(size=DIM)

    check = named(green(root, matrix[0], query_vector=elsewhere.tolist()), "canary")

    assert check.status is Status.FAIL
    assert "quietly worse" in check.detail


def test_the_canary_fails_when_the_reference_is_missing(built) -> None:
    root, matrix = built
    (root / "reference.npy").unlink()

    check = named(green(root, matrix[0]), "canary")

    assert check.status is Status.FAIL
    assert "only trusted" in check.detail


# -- 4. completeness ------------------------------------------------------------------------


def test_a_bundle_short_of_its_claimed_chunks_is_refused(built) -> None:
    """! "A bundle missing 400 documents must not load silently." The incumbent's
    extraction produced 994,602 records against a 748,558-row corpus and nothing recorded
    what happened to the missing quarter."""
    root, matrix = built
    document = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    document["chunk_count"] = 748_558
    (root / "manifest.json").write_text(json.dumps(document), encoding="utf-8")

    check = named(green(root, matrix[0]), "completeness")

    assert check.status is Status.FAIL
    assert "748,558" in check.detail


def test_failures_beyond_the_corpus_tolerance_are_refused(built) -> None:
    root, matrix = built
    document = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    document["inventory"]["counts"]["failed.jsonl"] = 400
    (root / "manifest.json").write_text(json.dumps(document), encoding="utf-8")

    check = named(green(root, matrix[0], failure_tolerance=0.01), "completeness")

    assert check.status is Status.FAIL
    assert "400" in check.detail


def test_a_tolerated_failure_rate_passes(built) -> None:
    root, matrix = built
    document = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    document["inventory"]["counts"]["failed.jsonl"] = 1
    (root / "manifest.json").write_text(json.dumps(document), encoding="utf-8")

    assert named(green(root, matrix[0], failure_tolerance=0.5), "completeness").ok


# -- 5. checksums ---------------------------------------------------------------------------


def test_an_altered_shard_is_caught(built) -> None:
    root, matrix = built
    target = root / "chunks" / "part-00000.parquet"
    target.write_bytes(target.read_bytes() + b"\x00")

    check = named(green(root, matrix[0]), "checksums")

    assert check.status is Status.FAIL
    assert "missing or altered" in check.detail


def test_a_shard_the_inventory_does_not_mention_is_caught(built) -> None:
    """! The two-generations bug. A re-run that appended shards beside stale ones loads
    both, nothing raises, and the corpus is silently larger than it should be."""
    root, matrix = built
    (root / "chunks" / "part-00001.parquet").write_bytes(b"stale generation")

    check = named(green(root, matrix[0]), "checksums")

    assert check.status is Status.FAIL
    assert "not in the inventory" in check.detail


# -- 6. disk · 7. provenance -------------------------------------------------------------------


def test_insufficient_disk_fails_before_the_copy_rather_than_during_it(built) -> None:
    """At ~0.8TB for 100M rows, running out mid-`COPY` rolls back after hours and leaves
    the disk still full."""
    root, matrix = built

    check = named(green(root, matrix[0], free_bytes=1024), "disk")

    assert check.status is Status.FAIL
    assert "projected load needs" in check.detail


def test_a_provenance_incomplete_bundle_fails_by_default(built, tmp_path: Path) -> None:
    """! The incumbent has no `source_url` on 100% of its rows and is worth loading as a
    scored baseline — flagged, never dressed up as verifiable."""
    root, matrix = built
    document = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    document["provenance_complete"] = False
    (root / "manifest.json").write_text(json.dumps(document), encoding="utf-8")

    assert named(green(root, matrix[0]), "provenance").status is Status.FAIL


def test_loading_it_anyway_downgrades_to_skipped_not_to_passed(built) -> None:
    """! The operator made a choice and the report keeps saying so: a bundle loaded under
    `allow_incomplete_provenance` still cannot report a clean preflight."""
    root, matrix = built
    document = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    document["provenance_complete"] = False
    (root / "manifest.json").write_text(json.dumps(document), encoding="utf-8")

    report = green(root, matrix[0], allow_incomplete_provenance=True)

    assert named(report, "provenance").status is Status.SKIPPED
    assert not report.passed()
    assert report.passed(allow_skipped=True)


# -- the plan ------------------------------------------------------------------------


def test_indexes_are_built_after_the_commit(built) -> None:
    """! `BUNDLE.md` §6. Index maintenance during `COPY` turns a 20-minute load into a
    six-hour one, and the ordering is structural here rather than remembered."""
    root, _ = built
    names = [s.name for s in load_plan(read_bundle(root), root)]

    assert names.index("indexes") > names.index("commit")
    assert names.index("copy chunks") < names.index("merge") < names.index("verify counts")


def test_the_count_check_happens_before_the_commit(built) -> None:
    """! A check that runs after the commit cannot honour "load nothing": by then the
    partial load exists and the only remedy is a truncate."""
    root, _ = built
    plan = load_plan(read_bundle(root), root)
    names = [s.name for s in plan]

    assert names.index("verify counts") < names.index("commit")
    assert next(s for s in plan if s.name == "verify counts").transactional
    assert not next(s for s in plan if s.name == "indexes").transactional


def test_vectors_are_merged_by_insert_rather_than_update(built) -> None:
    """A documented divergence from §6. An UPDATE over 100M rows writes a second version
    of every row and leaves the first as a dead tuple."""
    root, _ = built
    merge = next(s for s in load_plan(read_bundle(root), root) if s.name == "merge")

    assert merge.sql.startswith("INSERT INTO chunks")
    assert "UPDATE" not in merge.sql


def test_every_copy_names_its_columns() -> None:
    """! Positional `COPY` binds by order, so adding a schema column silently shifts every
    value one place — a corpus that loads without error and is wrong in every field."""
    statement = copy_from("chunks", ("id", "body"), "chunks/part-00000.parquet")

    assert '"id", "body"' in statement


def test_a_copy_reads_from_stdin_not_from_a_parquet_path() -> None:
    """! Postgres `COPY` reads text, CSV or its own binary format from a server-side file.
    A bundle holds parquet, so `FROM '<path>.parquet'` is not a slow load — it is a format
    the server has no reader for, and the statement fails at the first shard.

    The file still has to be named somewhere a reader can see it, so it is a comment and a
    `Step.inputs` entry rather than part of the statement.
    """
    statement = copy_from("chunks", ("id",), "chunks/part-00000.parquet")

    assert "FROM STDIN" in statement
    assert "'chunks/part-00000.parquet'" not in statement
    assert "-- from chunks/part-00000.parquet" in statement


def test_identifiers_and_literals_are_quoted() -> None:
    """The values come from a reviewed YAML file in git, and that is still not a reason to
    interpolate them raw."""
    assert quote_literal("O'Brien") == "'O''Brien'"
    assert quote_literal(None) == "NULL"
    assert quote_literal(True) == "TRUE"


def test_the_stamped_recipe_carries_the_fields_corpus_meta_was_missing(built) -> None:
    """`padding_side` and the three instruction columns — the two places Ravel's manifest
    is deliberately stricter than the table it was modelled on."""
    root, _ = built
    stamp = next(
        s for s in load_plan(read_bundle(root), root) if s.name == "stamp corpus_meta"
    )

    for column in (
        "dense_padding_side",
        "dense_instruction_style",
        "dense_doc_instruction",
        "dense_query_instruction",
        "manifest_sha256",
    ):
        assert f'"{column}"' in stamp.sql


def test_a_text_only_bundle_plans_a_load_without_vectors(tmp_path: Path) -> None:
    manifest = BundleManifest(
        corpus_id="text_only",
        run_id="r1",
        chunk_count=2,
        source_manifest_sha256="a" * 64,
        dense=spec_for(dim=DIM, id="q"),
    )
    writer = BundleWriter(root=tmp_path / "v1", manifest=manifest)
    rows = write_chunks(_chunks(2), str(writer.shard_path("chunks", 0)))
    writer.record("chunks/part-00000.parquet", rows)
    writer.write_failed([])
    writer.seal()

    names = [s.name for s in load_plan(read_bundle(writer.root), writer.root)]

    assert "copy vectors" not in names
    assert "merge" in names
