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
