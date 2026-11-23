"""A text dataset: finished chunks with their signals, and the sources they were read from.

`ravel export <corpus> --out DIR` writes what a dataset hub serves (a Hugging Face dataset,
say). Without `--vectors` it carries no embedding; with it, each row also carries the dense
vector `ravel embed` wrote for that chunk (`embedding`, float16 x dim, as stored):

    DIR/data/train-0000N-of-0000M.parquet   every chunk column, then every signal column
    DIR/sources/manifest.jsonl              one line per source: path, url, bytes, sha256
    DIR/sources/<snapshot>                  the snapshot list, when the corpus declares one

! The chunks are written as they are; nothing is recomputed. The signal sidecar is joined by
position and checked by id: the sidecar is written in chunk order, so a shard of chunks and
the matching slice of signals must name the same ids, and an export that would pair a chunk
with another chunk's signals fails instead.

! Vectors are joined by id, ✗ by position, from the generation `embed.run.locate_vectors`
names for this chunk generation, so they are the ones made from these chunks by the
corpus's declared embedder. A chunk with no vector fails the export: a dataset that is
partly embedded looks whole.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from extract.run import iter_canon
from runtime.shards import newest_generation

# ! A row group is what a reader must load whole to read any row of it, and the Hugging Face
# dataset viewer refuses one over 300 MB ("Scan size limit exceeded"). Written without a
# size, pyarrow put each shard in ONE row group: JP_LAWS_2611's were 515 MB and its viewer
# showed nothing; FR (343 MB) and DE (275 MB) were at the edge. Sized by bytes, ✗ rows: a
# row's size runs from a short German paragraph to a Japanese article with its vector.
ROW_GROUP_BYTES = 64 * 1024 * 1024


def row_group_rows(table: pa.Table, target: int = ROW_GROUP_BYTES) -> int:
    """Rows per row group so that one holds about `target` bytes, in memory, uncompressed."""
    rows = int(table.num_rows)
    if rows == 0:
        return 1
    per_row = max(1, int(table.nbytes) // rows)
    return max(1, min(rows, target // per_row))


class ExportError(RuntimeError):
    pass


@dataclass
class ExportReport:
    rows: int = 0
    shards: int = 0
    sources: int = 0
    columns: list[str] = field(default_factory=list)
    signals: str | None = None
    vectors: str | None = None


def export_dataset(
    workspace: Path | str,
    corpus_id: str,
    out_dir: Path | str,
    *,
    variant: str = "default",
    split: str = "train",
    source_root: Path | None = None,
    snapshot: Path | None = None,
    with_signals: bool = True,
    vector_dir: Path | None = None,
    chunk_dir: Path | None = None,
) -> ExportReport:
    """Write the dataset. `source_root` makes manifest paths relative, as the corpus states
    them; `snapshot` is copied beside the manifest.

    `chunk_dir` names the chunks to write instead of the workspace's newest generation: a
    sealed bundle's `chunks/`, whose rows are the ones a database prepared from it serves
    (a bundle may carry rows relabelled after the build, `bundle.relabel`)."""
    workspace, out_dir = Path(workspace), Path(out_dir)
    if chunk_dir is None:
        chunk_dir = newest_generation(workspace / "chunks" / corpus_id / variant)
    if chunk_dir is None:
        raise ExportError(f"no chunks for {corpus_id}/{variant} in {workspace}")
    shards = sorted(chunk_dir.glob("part-*.parquet"))

    signals: pa.Table | None = None
    report = ExportReport()
    if with_signals:
        signal_dir = workspace / "signals" / corpus_id / variant / chunk_dir.name
        parts = sorted(signal_dir.glob("part-*.parquet"))
        if not parts:
            raise ExportError(
                f"no signals for chunk generation {chunk_dir.name} at {signal_dir} · run "
                "`ravel signals` first, or export --no-signals"
            )
        signals = pa.concat_tables(pq.read_table(p) for p in parts)
        report.signals = signal_dir.as_posix()

    vectors: dict[str, int] = {}
    embedding: pa.ChunkedArray | None = None
    if vector_dir is not None:
        parts = sorted(Path(vector_dir).glob("part-*.parquet"))
        if not parts:
            raise ExportError(f"no vectors at {vector_dir} · run `ravel embed` first")
        stored = pa.concat_tables(pq.read_table(p) for p in parts)
        vectors = {cid: i for i, cid in enumerate(stored["chunk_id"].to_pylist())}
        embedding = stored["embedding"]
        report.vectors = Path(vector_dir).as_posix()

    data = out_dir / "data"
    data.mkdir(parents=True, exist_ok=True)
    for old in data.glob(f"{split}-*.parquet"):
        old.unlink()
    offset = 0
    for index, shard in enumerate(shards):
        table = pq.read_table(shard)
        if signals is not None:
            part = signals.slice(offset, table.num_rows)
            if part.num_rows != table.num_rows or not part["chunk_id"].equals(table["id"]):
                raise ExportError(
                    f"{shard.name}: the signal sidecar is not in this generation's chunk order"
                )
            for name in part.column_names[1:]:
                if name in table.column_names:
                    raise ExportError(f"signal column {name!r} collides with a chunk column")
                table = table.append_column(part.schema.field(name), part[name])
        if embedding is not None:
            ids = table["id"].to_pylist()
            absent = [cid for cid in ids if cid not in vectors]
            if absent:
                raise ExportError(
                    f"{shard.name}: {len(absent):,} chunks have no vector (first {absent[0]})"
                    " · finish `ravel embed`"
                )
            rows = pa.array([vectors[cid] for cid in ids], type=pa.int64())
            table = table.append_column("embedding", embedding.take(rows))
        offset += table.num_rows
        name = f"{split}-{index:05d}-of-{len(shards):05d}.parquet"
        # ! A page index too, so a reader can seek inside a row group (the viewer's advice).
        pq.write_table(
            table,
            data / name,
            compression="zstd",
            row_group_size=row_group_rows(table, ROW_GROUP_BYTES),
            write_page_index=True,
        )
        report.rows += table.num_rows
        report.shards += 1
        report.columns = table.column_names
    if signals is not None and offset != signals.num_rows:
        raise ExportError(f"{signals.num_rows:,} signal rows for {offset:,} chunks")

    sources = out_dir / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    with (sources / "manifest.jsonl").open("w", encoding="utf-8") as handle:
        for doc in iter_canon(workspace, corpus_id):
            src = doc.source
            path = _relative(Path(src.path), source_root)
            line = {
                "path": path.as_posix(),
                "url": src.url,
                "bytes": src.bytes,
                "sha256": src.sha256,
                "group": path.parent.name,
            }
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
            report.sources += 1
    if snapshot is not None and snapshot.is_file():
        (sources / snapshot.name).write_bytes(snapshot.read_bytes())
    return report


def _relative(path: Path, root: Path | None) -> Path:
    """`path` below `root`, compared as written and resolved; as it is when it is not."""
    if root is None:
        return path
    for candidate in (path, path.resolve()):
        for base in (root, root.resolve()):
            if candidate.is_relative_to(base):
                return candidate.relative_to(base)
    return path
