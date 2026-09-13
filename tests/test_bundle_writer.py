"""Writing a bundle: layout, the two hashes, and the refusals.

The recurring assertion here is that a bundle cannot be *quietly* wrong. Every failure
this file pins would otherwise produce a directory that looks like a bundle, loads without
error, and holds a corpus that is not the one the manifest describes.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from bundle import (
    BundleLayoutError,
    BundleManifest,
    BundleWriter,
    iter_vectors,
    read_bundle,
    write_clusters,
    write_domains,
    write_edges,
    write_signals,
    write_vectors,
)
from chunk.models import Chunk
from chunk.store import write_chunks
from embed import spec_for

DIM = 8


def manifest(**over: object) -> BundleManifest:
    base: dict[str, object] = {
        "corpus_id": "id_legal",
        "run_id": "r1",
        "chunk_count": 3,
        "source_manifest_sha256": "a" * 64,
        "dense": spec_for(dim=DIM, id="q"),
        "chunker": "unit",
        "chunker_version": "1.0",
    }
    return BundleManifest(**(base | over))  # type: ignore[arg-type]


def chunks(n: int = 3) -> list[Chunk]:
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


def vectors(n: int = 3, dim: int = DIM) -> np.ndarray:
    return np.random.default_rng(0).normal(size=(n, dim)).astype(np.float32)


def bundle(tmp_path: Path, **over: object) -> BundleWriter:
    return BundleWriter(root=tmp_path / "v1", manifest=manifest(**over))


def fill(writer: BundleWriter, *, n: int = 3, dim: int = DIM) -> np.ndarray:
    rows = write_chunks(chunks(n), str(writer.shard_path("chunks", 0)))
    writer.record("chunks/part-00000.parquet", rows)
    matrix = vectors(n, dim)
    rows = write_vectors(
        writer.shard_path("vectors", 0), [c.id for c in chunks(n)], matrix, dim=dim
    )
    writer.record("vectors/part-00000.parquet", rows)
    writer.write_reference(matrix[0], "Pasal 1")
    writer.write_failed([])
    return matrix


# -- the layout ----------------------------------------------------------------------


def test_a_sealed_bundle_carries_an_inventory_of_every_file(tmp_path: Path) -> None:
    writer = bundle(tmp_path)
    fill(writer)
    writer.seal()

    document = read_bundle(writer.root)
    inventory = document["inventory"]["checksums"]

    assert "chunks/part-00000.parquet" in inventory
    assert "vectors/part-00000.parquet" in inventory
    assert "schema.sql" in inventory
    assert all(len(digest) == 64 for digest in inventory.values())


def test_the_recipe_hash_survives_a_rebuild_and_the_inventory_does_not(
    tmp_path: Path,
) -> None:
    """! Two hashes, two jobs. The recipe answers "are these the same build?" and must
    ignore run ids and timestamps; the inventory answers "did this arrive intact?" and
    must not. One hash cannot do both, and the incumbent had neither."""
    first = bundle(tmp_path / "a")
    fill(first)
    first.seal()
    second = BundleWriter(root=tmp_path / "b" / "v1", manifest=manifest(run_id="r2"))
    fill(second)
    second.seal()

    a, b = read_bundle(first.root), read_bundle(second.root)

    assert a["manifest_sha256"] == b["manifest_sha256"], "same recipe"
    assert a["run_id"] != b["run_id"]


def test_a_directory_that_already_holds_a_manifest_is_refused(tmp_path: Path) -> None:
    """! `CLAUDE.md` §7.10. Re-running into an existing version directory leaves two
    generations of shards where the next stage reads both — 931 documents became 1,862
    the last time this happened."""
    writer = bundle(tmp_path)
    fill(writer)
    writer.seal()

    with pytest.raises(BundleLayoutError, match="never edited"):
        BundleWriter(root=writer.root, manifest=manifest())


def test_a_bundle_is_sealed_once(tmp_path: Path) -> None:
    writer = bundle(tmp_path)
    fill(writer)
    writer.seal()

    with pytest.raises(BundleLayoutError, match="already been sealed"):
        writer.seal()


def test_nothing_is_a_bundle_until_it_is_sealed(tmp_path: Path) -> None:
    """! The manifest is written last, so a run that dies mid-shard leaves a directory
    that can be cleared and retried rather than one that looks complete."""
    writer = bundle(tmp_path)
    fill(writer)

    assert not (writer.root / "manifest.json").exists()
    with pytest.raises(BundleLayoutError, match="unsealed"):
        read_bundle(writer.root)


def test_failed_jsonl_is_written_even_when_nothing_failed(tmp_path: Path) -> None:
    """! An absent file is ambiguous between "nothing failed" and "nothing was recorded",
    and the completeness check has to tell those apart to be worth running."""
    writer = bundle(tmp_path)
    fill(writer)
    writer.seal()

    assert (writer.root / "failed.jsonl").exists()
    assert read_bundle(writer.root)["inventory"]["counts"]["failed.jsonl"] == 0


# -- what the writer refuses ------------------------------------------------------------


def test_sealing_refuses_a_manifest_that_disagrees_with_its_own_shards(
    tmp_path: Path,
) -> None:
    """! Fail here, where the cause is still on screen, rather than at load time."""
    writer = bundle(tmp_path, chunk_count=748_558)
    fill(writer)

    with pytest.raises(BundleLayoutError, match="748,558"):
        writer.seal()


def test_sealing_refuses_more_chunks_than_vectors(tmp_path: Path) -> None:
    """! The load-time join would drop the difference without reporting it."""
    writer = bundle(tmp_path, chunk_count=3)
    rows = write_chunks(chunks(3), str(writer.shard_path("chunks", 0)))
    writer.record("chunks/part-00000.parquet", rows)
    rows = write_vectors(
        writer.shard_path("vectors", 0), ["c0", "c1"], vectors(2), dim=DIM
    )
    writer.record("vectors/part-00000.parquet", rows)

    with pytest.raises(BundleLayoutError, match="would drop the difference"):
        writer.seal()


def test_a_vector_of_the_wrong_width_is_refused(tmp_path: Path) -> None:
    """A bundle and a database are married by dimension (`EMBEDDING.md` §2)."""
    with pytest.raises(BundleLayoutError, match="different spaces"):
        write_vectors(tmp_path / "v.parquet", ["a"], vectors(1, 16), dim=DIM)


def test_ids_that_do_not_line_up_with_vectors_are_refused(tmp_path: Path) -> None:
    """! A mis-joined vector shard is not an error at load — it is a silent mis-join."""
    with pytest.raises(BundleLayoutError, match="do not line up"):
        write_vectors(tmp_path / "v.parquet", ["a", "b"], vectors(3), dim=DIM)


def test_an_unknown_vector_dtype_is_refused(tmp_path: Path) -> None:
    with pytest.raises(BundleLayoutError, match="unknown vector dtype"):
        write_vectors(tmp_path / "v.parquet", ["a"], vectors(1), dim=DIM, dtype="bfloat16")


def test_a_cluster_table_cannot_describe_a_structure_that_was_not_built(
    tmp_path: Path,
) -> None:
    with pytest.raises(BundleLayoutError, match="row counts"):
        write_clusters(
            tmp_path / "c.parquet",
            np.eye(3, DIM, dtype=np.float32),
            [10, 20],
            domain_id="d",
            generation=1,
        )


# -- what comes back --------------------------------------------------------------------


def test_vectors_round_trip_at_the_declared_width(tmp_path: Path) -> None:
    """! Fixed-size list, so a short row is a write error rather than a row that loads and
    means something else."""
    matrix = vectors(3)
    write_vectors(tmp_path / "v.parquet", ["a", "b", "c"], matrix, dim=DIM, dtype="float32")

    back = dict(iter_vectors(tmp_path / "v.parquet"))

    assert list(back) == ["a", "b", "c"]
    assert all(v.shape == (DIM,) for v in back.values())
    assert np.allclose(back["a"], matrix[0])


def test_the_canary_is_stored_at_full_precision_not_storage_precision(
    tmp_path: Path,
) -> None:
    """! The canary tests whether Vera's query embedder is the same *model*. Rounding it
    to the corpus's fp16 storage first would fold the disk format's error into a
    comparison that is about the model."""
    writer = bundle(tmp_path)
    matrix = fill(writer)
    writer.seal()

    stored = np.load(writer.root / "reference.npy")

    assert stored.dtype == np.float32
    assert np.array_equal(stored, matrix[0])
    assert (writer.root / "reference.txt").read_text(encoding="utf-8") == "Pasal 1"


def test_a_dangling_citation_stays_null_rather_than_being_guessed(tmp_path: Path) -> None:
    """! Roughly half of what an Indonesian regulation cites is outside any given
    collection. A guessed `resolved_doc_id` puts a wrong edge in a graph whose entire
    value is that its edges are real."""
    import pyarrow.parquet as pq

    write_edges(
        tmp_path / "edges.parquet",
        [
            {
                "src_chunk_id": "c0",
                "src_doc_id": "d0",
                "kind": "regulation",
                "target": "UU 40/2007",
                "text": "Undang-Undang Nomor 40 Tahun 2007",
                "start": 25,
                "end": 58,
                "resolved_doc_id": None,
            }
        ],
    )
    row = pq.read_table(tmp_path / "edges.parquet").to_pylist()[0]

    assert row["resolved_doc_id"] is None
    assert row["start"] == 25 and row["end"] == 58


def test_signals_carry_json_so_the_enricher_set_can_change(tmp_path: Path) -> None:
    """A table whose columns change per variant is not a schema (`CLAUDE.md` §5.4)."""
    import pyarrow.parquet as pq

    write_signals(
        tmp_path / "s.parquet",
        [("c0", {"authority": 0.2, "regulation": 1}), ("c1", {"completeness": 0.0})],
    )
    rows = pq.read_table(tmp_path / "s.parquet").to_pylist()

    assert json.loads(rows[0]["data"])["authority"] == 0.2
    assert "authority" not in json.loads(rows[1]["data"])


def test_a_domain_carries_the_evidence_for_its_threshold(tmp_path: Path) -> None:
    """! A threshold without the distributions it came from is a magic number, and the
    next person to touch it has only a guess (`CLUSTERING.md` §4)."""
    import pyarrow.parquet as pq

    write_domains(
        tmp_path / "d.parquet",
        [
            {
                "domain_id": "id_legal",
                "description": "Indonesian regulations, national and regional.",
                "anchor_index": 0,
                "anchor": [0.1] * DIM,
                "threshold": 0.42,
                "calibration": {"false_reject": 0.05, "false_accept": 0.01},
                "row_count": 3,
            }
        ],
    )
    row = pq.read_table(tmp_path / "d.parquet").to_pylist()[0]

    assert json.loads(row["calibration"])["false_reject"] == 0.05
    assert row["description"].startswith("Indonesian")
