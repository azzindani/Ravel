"""A small, complete, sealed bundle built from a description of its documents.

For tests and CI that need two versions of one corpus: the same recipe, different documents.
Vectors are a pure function of the chunk id, so a document that appears in both versions is
byte-identical in both, which is what a real rebuild of unchanged sources gives.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bundle import (  # noqa: E402
    BundleManifest,
    BundleWriter,
    SparseSpec,
    write_doc_quality,
    write_doc_status,
    write_relations,
    write_vectors,
)
from chunk.models import Chunk  # noqa: E402
from chunk.store import write_chunks  # noqa: E402
from embed import spec_for  # noqa: E402

__all__ = ["DIM", "CORPUS", "DocSpec", "build_corpus"]

DIM = 16
CORPUS = "delta_fixture"


@dataclass(frozen=True)
class DocSpec:
    chunks: int = 2
    indexable: bool = True
    revokes: str | None = None
    """`doc_id` of the document this one revokes: one relation row, and the target's status."""


def _vector(chunk_id: str) -> np.ndarray[Any, Any]:
    seed = int.from_bytes(hashlib.md5(chunk_id.encode()).digest()[:4], "little")
    return np.random.default_rng(seed).normal(size=DIM).astype(np.float32)


def build_corpus(
    root: Path,
    docs: dict[str, DocSpec],
    *,
    name: str,
    model: str = "ci-hash",
) -> Path:
    """Seal a bundle of `docs` at `root / name` and return its path."""
    ordered = sorted(docs)
    chunk_ids = {d: [f"{d}-{i}" for i in range(docs[d].chunks)] for d in ordered}
    total = sum(len(v) for v in chunk_ids.values())
    manifest = BundleManifest(
        corpus_id=CORPUS,
        run_id=name,
        chunk_count=total,
        source_manifest_sha256=hashlib.sha256("\n".join(ordered).encode()).hexdigest(),
        dense=spec_for(dim=DIM, id=model),
        sparse=SparseSpec(
            scheme="bm25", dim=2048, k1=1.5, b=0.75, vocab_sha256="b" * 64, fit_docs=total
        ),
        chunker="unit",
        chunker_version="1.0",
        profile_ref="id_regulation@1.0",
        text_search_config="simple",
    )
    writer = BundleWriter(root=root / name, manifest=manifest)

    chunks = [
        Chunk(
            id=cid,
            doc_id=d,
            body=f"Pasal {i + 1} dokumen {d} memuat ketentuan yang wajib ditaati.",
            token_count=8,
            source_title=f"Peraturan {d}",
            source_url=f"https://example.test/{d}",
            source_sha256=hashlib.sha256(d.encode()).hexdigest(),
            identifier=f"Perbup {d}",
            chunker="unit",
            chunker_version="1.0",
            config_hash="h",
        )
        for d in ordered
        for i, cid in enumerate(chunk_ids[d])
    ]
    writer.record(
        "chunks/part-00000.parquet", write_chunks(chunks, str(writer.shard_path("chunks", 0)))
    )
    ids = [c.id for c in chunks]
    matrix = np.stack([_vector(c) for c in ids])
    writer.record(
        "vectors/part-00000.parquet",
        write_vectors(writer.shard_path("vectors", 0), ids, matrix, dim=DIM),
    )
    pq.write_table(
        pa.table(
            {
                "chunk_id": ids,
                "sparse": [
                    f"{{{i % 2000 + 1}:0.5,{i % 1000 + 10}:1.25}}/2048" for i in range(total)
                ],
            }
        ),
        str(writer.shard_path("sparse", 0)),
    )
    writer.record("sparse/part-00000.parquet", total)

    relations: list[dict[str, Any]] = [
        {
            "source_doc_id": d,
            "relation": "revokes",
            "target_text": f"Peraturan {docs[d].revokes}",
            "target_type": "PERATURAN BUPATI",
            "target_number": "1",
            "target_year": 2020,
            "target_doc_id": docs[d].revokes if docs[d].revokes in docs else None,
            "resolution": "resolved" if docs[d].revokes in docs else "unresolved",
            "confidence": 1.0,
            "partial": False,
            "mentions": 1,
            "evidence_chunk_id": chunk_ids[d][0],
            "quote": f"Peraturan {docs[d].revokes} dicabut dan dinyatakan tidak berlaku",
        }
        for d in ordered
        if docs[d].revokes is not None and docs[d].chunks
    ]
    writer.record(
        "relations.parquet", write_relations(writer.root / "relations.parquet", relations)
    )
    revoked_by: dict[str, list[str]] = {}
    for row in relations:
        if row["target_doc_id"]:
            revoked_by.setdefault(str(row["target_doc_id"]), []).append(
                str(row["source_doc_id"])
            )
    writer.record(
        "doc_status.parquet",
        write_doc_status(
            writer.root / "doc_status.parquet",
            [
                {
                    "doc_id": d,
                    "status": "revoked" if d in revoked_by else "none_known",
                    "revoked_by": revoked_by.get(d, []),
                    "amended_by": [],
                }
                for d in ordered
            ],
        ),
    )
    writer.record(
        "doc_quality.parquet",
        write_doc_quality(
            writer.root / "doc_quality.parquet",
            [
                {
                    "doc_id": d,
                    "verdict": "clean" if docs[d].indexable else "unusable",
                    "indexable": docs[d].indexable,
                    "chunks": docs[d].chunks,
                    "damaged_chunks": 0 if docs[d].indexable else docs[d].chunks,
                    "words": 100,
                    "unknown_share": 0.01 if docs[d].indexable else 0.6,
                    "damaged_share": 0.0 if docs[d].indexable else 1.0,
                    "replacement_per_1000": 0.0,
                    "reasons": [],
                }
                for d in ordered
            ],
        ),
    )
    writer.write_reference(matrix[0], "Pasal 1")
    writer.write_failed([])
    writer.seal()
    return writer.root
