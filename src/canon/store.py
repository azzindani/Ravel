"""Canonical documents on disk: sharded parquet, one ROW per document.

! Never one file per document. Object stores and HF datasets both degrade badly
at millions of small objects — 30K documents as 30K files is 30K uploads and 30K
listings on every resume (EXECUTION.md §4, INTERFACES.md §5).

Open-ended maps (`attrs`, `params`, `recipe`) are stored as JSON strings rather
than parquet structs: their keys are per-extractor and unknowable at schema time,
and a struct that changes shape per row is not a schema. Everything with a known
shape is a real column, so `blocks` and `tables` stay queryable without decoding.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from canon.models import CanonicalDoc

# ! Variable-length, not fixed_size_list(4). A fixed-size list cannot represent a
# null element nested inside a struct inside a list — pyarrow turns the None into a
# zero-length list and then rejects it. Length is guaranteed by the model
# (tuple[float, float, float, float]), so the schema does not need to restate it.
_BBOX = pa.list_(pa.float64())

_BLOCK = pa.struct(
    [
        ("id", pa.string()),
        ("type", pa.string()),
        ("text", pa.large_string()),
        ("level", pa.int8()),
        ("heading_path", pa.list_(pa.string())),
        ("page", pa.int32()),
        ("bbox", _BBOX),
        ("reading_order", pa.int32()),
        ("confidence", pa.float32()),
        ("attrs", pa.string()),  # JSON
        ("ref", pa.string()),
    ]
)

_CELL = pa.struct(
    [
        ("r", pa.int32()),
        ("c", pa.int32()),
        ("rowspan", pa.int32()),
        ("colspan", pa.int32()),
        ("text", pa.large_string()),
    ]
)

_TABLE = pa.struct(
    [
        ("id", pa.string()),
        ("page", pa.int32()),
        ("bbox", _BBOX),
        ("caption", pa.string()),
        ("n_rows", pa.int32()),
        ("n_cols", pa.int32()),
        ("header_rows", pa.int32()),
        ("cells", pa.list_(_CELL)),
        ("confidence", pa.float32()),
    ]
)

_ASSET = pa.struct(
    [
        ("id", pa.string()),
        ("kind", pa.string()),
        ("page", pa.int32()),
        ("bbox", _BBOX),
        ("recipe", pa.string()),  # JSON
    ]
)

SCHEMA = pa.schema(
    [
        ("canon_version", pa.string()),
        ("doc_id", pa.string()),
        # Source — flattened, so a shard can be filtered without decoding blocks.
        ("source_path", pa.string()),
        ("source_url", pa.string()),
        ("source_title", pa.large_string()),
        ("source_mime", pa.string()),
        ("source_bytes", pa.int64()),
        ("source_sha256", pa.string()),
        # Extraction — the cache-key inputs, also flattened for auditing a corpus
        # by extractor without reading the bodies.
        ("extractor", pa.string()),
        ("extractor_version", pa.string()),
        ("model", pa.string()),
        ("params", pa.string()),  # JSON
        ("extracted_at", pa.timestamp("us", tz="UTC")),
        ("pages", pa.int32()),
        ("warnings", pa.list_(pa.string())),
        # Body
        ("blocks", pa.list_(_BLOCK)),
        ("tables", pa.list_(_TABLE)),
        ("assets", pa.list_(_ASSET)),
    ]
)


def _row(doc: CanonicalDoc) -> dict[str, Any]:
    s, e = doc.source, doc.extraction
    return {
        "canon_version": doc.canon_version,
        "doc_id": doc.doc_id,
        "source_path": s.path,
        "source_url": s.url,
        "source_title": s.title,
        "source_mime": s.mime,
        "source_bytes": s.bytes,
        "source_sha256": s.sha256,
        "extractor": e.extractor,
        "extractor_version": e.extractor_version,
        "model": e.model,
        "params": json.dumps(e.params, sort_keys=True, ensure_ascii=False),
        "extracted_at": e.extracted_at,
        "pages": e.pages,
        "warnings": e.warnings,
        "blocks": [
            {
                "id": b.id,
                "type": b.type.value,
                "text": b.text,
                "level": b.level,
                "heading_path": b.heading_path,
                "page": b.page,
                "bbox": list(b.bbox) if b.bbox else None,
                "reading_order": b.reading_order,
                "confidence": b.confidence,
                "attrs": json.dumps(b.attrs, sort_keys=True, ensure_ascii=False),
                "ref": b.ref,
            }
            for b in doc.blocks
        ],
        "tables": [
            {
                "id": t.id,
                "page": t.page,
                "bbox": list(t.bbox) if t.bbox else None,
                "caption": t.caption,
                "n_rows": t.n_rows,
                "n_cols": t.n_cols,
                "header_rows": t.header_rows,
                "cells": [c.model_dump() for c in t.cells],
                "confidence": t.confidence,
            }
            for t in doc.tables
        ],
        "assets": [
            {
                "id": a.id,
                "kind": a.kind,
                "page": a.page,
                "bbox": list(a.bbox) if a.bbox else None,
                "recipe": json.dumps(a.recipe, sort_keys=True, ensure_ascii=False),
            }
            for a in doc.assets
        ],
    }


def _doc(row: dict[str, Any]) -> CanonicalDoc:
    return CanonicalDoc.model_validate(
        {
            "canon_version": row["canon_version"],
            "doc_id": row["doc_id"],
            "source": {
                "path": row["source_path"],
                "url": row["source_url"],
                "title": row["source_title"],
                "mime": row["source_mime"],
                "bytes": row["source_bytes"],
                "sha256": row["source_sha256"],
            },
            "extraction": {
                "extractor": row["extractor"],
                "extractor_version": row["extractor_version"],
                "model": row["model"],
                "params": json.loads(row["params"] or "{}"),
                "extracted_at": row["extracted_at"],
                "pages": row["pages"],
                "warnings": row["warnings"] or [],
            },
            "blocks": [
                {**b, "attrs": json.loads(b["attrs"] or "{}"),
                 "bbox": tuple(b["bbox"]) if b["bbox"] else None}
                for b in row["blocks"] or []
            ],
            "tables": [
                {**t, "bbox": tuple(t["bbox"]) if t["bbox"] else None}
                for t in row["tables"] or []
            ],
            "assets": [
                {**a, "recipe": json.loads(a["recipe"] or "{}"),
                 "bbox": tuple(a["bbox"]) if a["bbox"] else None}
                for a in row["assets"] or []
            ],
        }
    )


def to_table(docs: Iterable[CanonicalDoc]) -> pa.Table:
    return pa.Table.from_pylist([_row(d) for d in docs], schema=SCHEMA)


def write_shard(docs: Iterable[CanonicalDoc], path: str, *, compression: str = "zstd") -> int:
    """Write one shard. Returns the row count."""
    table = to_table(docs)
    pq.write_table(table, path, compression=compression)
    return table.num_rows


def read_shard(path: str) -> list[CanonicalDoc]:
    return [_doc(r) for r in pq.read_table(path, schema=SCHEMA).to_pylist()]


def iter_shard(path: str, *, batch_size: int = 64) -> Iterator[CanonicalDoc]:
    """Stream a shard without materializing it — shards are 256MB–1GB."""
    for batch in pq.ParquetFile(path).iter_batches(batch_size=batch_size):
        for row in batch.to_pylist():
            yield _doc(row)
