"""Chunks on disk: sharded parquet, one ROW per chunk.

The same shape as `canon.store` and for the same reasons — many rows per file, an
explicit schema, open-ended maps as JSON strings. The difference is what happens next:
these columns are what Vera's loader reads, so the schema here is a contract, not an
implementation detail. Ravel writes it and Vera reads it (`CLAUDE.md`, closing note).

Provenance columns are hard columns, never packed into `attrs`. A field inside a JSON
blob cannot be indexed, cannot be constrained NOT NULL, and cannot be checked by a
preflight — and provenance is the one thing in this project that must be all three.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator

import pyarrow as pa
import pyarrow.parquet as pq

from chunk.models import Chunk

SCHEMA = pa.schema(
    [
        ("id", pa.string()),
        ("doc_id", pa.string()),
        ("body", pa.large_string()),
        ("token_count", pa.int32()),
        ("part_n", pa.int16()),
        ("part_of", pa.int16()),
        ("block_ids", pa.list_(pa.string())),
        # -- provenance: hard columns, not nullable except where the model allows --
        ("source_title", pa.large_string()),
        ("source_url", pa.string()),
        ("source_sha256", pa.string()),
        ("locator_page", pa.int32()),
        ("locator_section", pa.string()),
        ("heading_path", pa.string()),
        ("identifier", pa.string()),
        # -- lineage --
        ("chunker", pa.string()),
        ("chunker_version", pa.string()),
        ("config_hash", pa.string()),
        ("profile", pa.string()),
        ("attrs", pa.string()),  # JSON
    ]
)


def _row(chunk: Chunk) -> dict[str, object]:
    return {
        "id": chunk.id,
        "doc_id": chunk.doc_id,
        "body": chunk.body,
        "token_count": chunk.token_count,
        "part_n": chunk.part[0],
        "part_of": chunk.part[1],
        "block_ids": chunk.block_ids,
        "source_title": chunk.source_title,
        "source_url": chunk.source_url,
        "source_sha256": chunk.source_sha256,
        "locator_page": chunk.locator_page,
        "locator_section": chunk.locator_section,
        "heading_path": chunk.heading_path,
        "identifier": chunk.identifier,
        "chunker": chunk.chunker,
        "chunker_version": chunk.chunker_version,
        "config_hash": chunk.config_hash,
        "profile": chunk.profile,
        "attrs": json.dumps(chunk.attrs, ensure_ascii=False, sort_keys=True),
    }


def _chunk(row: dict[str, object]) -> Chunk:
    return Chunk(
        id=str(row["id"]),
        doc_id=str(row["doc_id"]),
        body=str(row["body"]),
        token_count=int(row["token_count"]),  # type: ignore[arg-type]
        part=(int(row["part_n"]), int(row["part_of"])),  # type: ignore[arg-type]
        block_ids=list(row["block_ids"] or []),  # type: ignore[arg-type]
        source_title=str(row["source_title"]),
        source_url=str(row["source_url"]),
        source_sha256=str(row["source_sha256"]),
        locator_page=row["locator_page"],  # type: ignore[arg-type]
        locator_section=row["locator_section"],  # type: ignore[arg-type]
        heading_path=row["heading_path"],  # type: ignore[arg-type]
        identifier=row["identifier"],  # type: ignore[arg-type]
        chunker=str(row["chunker"]),
        chunker_version=str(row["chunker_version"]),
        config_hash=str(row["config_hash"]),
        profile=row["profile"],  # type: ignore[arg-type]
        attrs=json.loads(str(row["attrs"] or "{}")),
    )


def write_chunks(chunks: Iterable[Chunk], path: str) -> int:
    table = pa.Table.from_pylist([_row(c) for c in chunks], schema=SCHEMA)
    pq.write_table(table, path, compression="zstd")
    return table.num_rows


def read_chunks(path: str) -> list[Chunk]:
    return [_chunk(row) for row in pq.read_table(path, schema=SCHEMA).to_pylist()]


def iter_chunks(path: str, batch_size: int = 2048) -> Iterator[Chunk]:
    """Stream a shard without materializing it. Phase C reads millions of these."""
    for batch in pq.ParquetFile(path).iter_batches(batch_size=batch_size):
        for row in batch.to_pylist():
            yield _chunk(row)
