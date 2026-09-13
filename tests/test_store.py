"""Sharded parquet storage: a document must survive the disk unchanged."""

from __future__ import annotations

from pathlib import Path

from canon import CanonicalDoc, iter_shard, read_shard, write_shard
from canon.store import SCHEMA, to_table


def test_shard_round_trip_is_lossless(doc: CanonicalDoc, tmp_path: Path) -> None:
    path = str(tmp_path / "part-00000.parquet")
    assert write_shard([doc], path) == 1

    (restored,) = read_shard(path)
    assert restored.model_dump() == doc.model_dump()


def test_many_documents_share_one_shard(doc: CanonicalDoc, tmp_path: Path) -> None:
    """! One row per document, never one file per document (EXECUTION.md §4)."""
    docs = []
    for i in range(25):
        clone = doc.model_copy(deep=True)
        sha = f"{i:064x}"
        clone.source.sha256 = sha
        clone.doc_id = f"sha256:{sha}"
        docs.append(clone)

    path = str(tmp_path / "part-00000.parquet")
    assert write_shard(docs, path) == 25
    assert len(list(tmp_path.iterdir())) == 1

    ids = [d.doc_id for d in iter_shard(path, batch_size=4)]
    assert ids == [d.doc_id for d in docs]


def test_schema_is_explicit_not_inferred(doc: CanonicalDoc) -> None:
    """An inferred schema changes shape with the data; this one must not."""
    assert to_table([doc]).schema.equals(SCHEMA)


def test_open_ended_maps_survive_as_json(doc: CanonicalDoc, tmp_path: Path) -> None:
    doc.blocks[2].attrs = {"numbering": "9", "is_ayat": False, "depth": 3}
    path = str(tmp_path / "part-00000.parquet")
    write_shard([doc], path)

    (restored,) = read_shard(path)
    assert restored.blocks[2].attrs == {"numbering": "9", "is_ayat": False, "depth": 3}


def test_empty_shard_is_still_a_valid_shard(tmp_path: Path) -> None:
    path = str(tmp_path / "part-00000.parquet")
    assert write_shard([], path) == 0
    assert read_shard(path) == []
