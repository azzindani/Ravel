"""`ravel export`: chunks joined with their signals by position, checked by id."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from bundle.dataset import ExportError, export_dataset


def _workspace(tmp_path: Path, signal_ids: list[str]) -> Path:
    chunks = tmp_path / "chunks" / "c" / "default" / "g1"
    chunks.mkdir(parents=True)
    first = pa.table({"id": ["a", "b"], "body": ["x", "y"]})
    pq.write_table(first, chunks / "part-00000.parquet")
    pq.write_table(pa.table({"id": ["c"], "body": ["z"]}), chunks / "part-00001.parquet")
    signals = tmp_path / "signals" / "c" / "default" / "g1"
    signals.mkdir(parents=True)
    table = pa.table(
        {
            "chunk_id": signal_ids,
            "refs": pa.array([["1 CFR 1.1"], [], ["2 CFR 2.2"]], pa.list_(pa.string())),
        }
    )
    pq.write_table(table, signals / "part-00000.parquet")
    return tmp_path


def test_signals_join_each_shard(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, ["a", "b", "c"])
    report = export_dataset(ws, "c", tmp_path / "out")
    assert (report.rows, report.shards) == (3, 2)
    second = pq.read_table(tmp_path / "out" / "data" / "train-00001-of-00002.parquet")
    assert second.column_names == ["id", "body", "refs"]
    assert second.to_pylist() == [{"id": "c", "body": "z", "refs": ["2 CFR 2.2"]}]


def test_signals_out_of_order_fail(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, ["b", "a", "c"])
    with pytest.raises(ExportError, match="chunk order"):
        export_dataset(ws, "c", tmp_path / "out")


def test_no_signals_is_said(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, ["a", "b", "c"])
    for part in (ws / "signals").rglob("*.parquet"):
        part.unlink()
    with pytest.raises(ExportError, match="ravel signals"):
        export_dataset(ws, "c", tmp_path / "out")
    assert export_dataset(ws, "c", tmp_path / "out", with_signals=False).rows == 3


def _vectors(tmp_path: Path, ids: list[str]) -> Path:
    d = tmp_path / "vectors" / "c" / "default" / "v1"
    d.mkdir(parents=True)
    vec = pa.list_(pa.float16(), 2)
    values = pa.array([float(i) for i in range(2 * len(ids))], pa.float16())
    table = pa.table(
        {"chunk_id": ids, "embedding": pa.FixedSizeListArray.from_arrays(values, 2)}
    )
    assert table.schema.field("embedding").type == vec
    # Written out of chunk order: the join is by id.
    pq.write_table(table.take(pa.array(list(range(len(ids)))[::-1])), d / "part-00000.parquet")
    return d


def test_vectors_join_by_id(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, ["a", "b", "c"])
    report = export_dataset(
        ws, "c", tmp_path / "out", vector_dir=_vectors(tmp_path, ["a", "b", "c"])
    )
    first = pq.read_table(tmp_path / "out" / "data" / "train-00000-of-00002.parquet")
    assert first.column_names == ["id", "body", "refs", "embedding"]
    assert first["embedding"].to_pylist() == [[0.0, 1.0], [2.0, 3.0]]
    assert report.vectors is not None


def test_a_chunk_without_a_vector_fails(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, ["a", "b", "c"])
    with pytest.raises(ExportError, match="no vector"):
        export_dataset(ws, "c", tmp_path / "out", vector_dir=_vectors(tmp_path, ["a", "b"]))


def test_a_row_group_is_sized_by_bytes_so_a_viewer_can_read_it() -> None:
    # ! JP_LAWS_2611 was written one 515 MB row group per shard and the dataset viewer,
    # which reads at most 300 MB at once, showed nothing.
    from bundle.dataset import row_group_rows

    table = pa.table({"body": ["x" * 1000] * 5000})
    rows = row_group_rows(table, target=100_000)
    assert 50 <= rows <= 100, rows
    assert row_group_rows(table, target=10**12) == table.num_rows
    assert row_group_rows(pa.table({"body": pa.array([], pa.string())})) == 1


def test_the_written_shards_carry_bounded_row_groups(tmp_path: Path, monkeypatch) -> None:
    import bundle.dataset as ds

    monkeypatch.setattr(ds, "ROW_GROUP_BYTES", 1)
    test_signals_join_each_shard(tmp_path)
    for shard in (tmp_path).rglob("data/*.parquet"):
        meta = pq.ParquetFile(shard).metadata
        assert meta.num_row_groups == meta.num_rows, "one row per group at a 1-byte target"
