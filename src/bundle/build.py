"""Phase C, assembled: the chunk shards, the vector shards and the sidecars become one sealed
bundle.

Nothing is recomputed here. The chunks were sealed by `ravel chunk`, the vectors by `ravel
embed`, the sidecars by their own tools; this module only checks that they describe the same
corpus and lays them out the way `BUNDLE.md` says, then seals the manifest. The checks are
the point, because each one is a way a corpus that took a GPU-day to build loads wrongly:

* **Every chunk has exactly one vector, and every vector a chunk.** Compared as sets of ids,
  not as row counts: equal counts with different ids is a corpus whose vectors belong to
  other chunks, and the load-time join would not notice until a query returned nonsense.
* **A sidecar row for a document the corpus does not hold is dropped, and said.** The
  quality and relations tools ran over every source file, duplicates and empties included;
  the corpus holds the chunks that survived. A row for a document with no chunks has nothing
  to apply to, and a duplicated `doc_id` would break the table's key at load time.
* **The source identity is derived from what the chunks cite.** `doc_id` is the sha256 of the
  source bytes, so a hash over the sorted set of them changes exactly when a source file
  does.

Clustering is not here: the load plan has no step that carries cluster tables into the
database, and a bundle section nothing reads is a section that rots.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

from bundle.manifest import BundleManifest
from bundle.writer import (
    BundleWriter,
    write_doc_quality,
    write_doc_status,
    write_relations,
)
from chunk.store import iter_chunks
from embed.factory import spec_of
from embed.run import locate_vectors
from runtime.ledger import Ledger
from spec import Registry, default_registry
from spec.corpus import CorpusSpec

__all__ = ["BuildReport", "BundleBuildError", "build_bundle"]


class BundleBuildError(RuntimeError):
    """The inputs do not describe one corpus. Nothing was sealed."""


@dataclass
class BuildReport:
    root: Path
    chunks: int = 0
    vectors: int = 0
    documents: int = 0
    failed: int = 0
    sidecars: dict[str, int] = field(default_factory=dict)
    dropped: dict[str, int] = field(default_factory=dict)
    manifest_sha256: str = ""
    reference: bool = False


def _shards(directory: Path) -> list[Path]:
    found = sorted(directory.glob("part-*.parquet"))
    if not found:
        raise BundleBuildError(f"no shards in {directory}")
    return found


def _column(paths: list[Path], name: str) -> Iterator[str]:
    for path in paths:
        yield from pq.read_table(str(path), columns=[name]).column(name).to_pylist()


def _rows(path: Path | None) -> list[dict[str, Any]]:
    return [] if path is None else list(pq.read_table(str(path)).to_pylist())


def build_bundle(
    corpus: CorpusSpec,
    *,
    workspace: Path | str,
    root: Path | str,
    variant: str = "default",
    relations: Path | None = None,
    status: Path | None = None,
    quality: Path | None = None,
    reference: tuple[np.ndarray[Any, Any], str] | None = None,
    run_id: str = "",
    text_search_config: str = "indonesian",
    notes: str = "",
    registry: Registry | None = None,
) -> BuildReport:
    """Assemble and seal one bundle at `root`."""
    if corpus.embed is None:
        raise BundleBuildError(f"{corpus.id}: no `embed:` section, so no vectors to bundle")
    registry = registry or default_registry()
    workspace = Path(workspace)
    chunk_dir, vector_dir = locate_vectors(
        corpus, workspace=workspace, variant=variant, registry=registry
    )
    if not vector_dir.is_dir():
        raise BundleBuildError(f"no vectors at {vector_dir} · run `ravel embed` first")
    chunk_shards = _shards(chunk_dir)
    vector_shards = _shards(vector_dir)

    # -- the two halves must describe the same corpus ---------------------------------
    chunk_ids = list(_column(chunk_shards, "id"))
    vector_ids = list(_column(vector_shards, "chunk_id"))
    if len(chunk_ids) != len(set(chunk_ids)):
        raise BundleBuildError("a chunk id appears twice in the chunk shards")
    if len(vector_ids) != len(set(vector_ids)):
        raise BundleBuildError("a vector exists twice for one chunk")
    if set(chunk_ids) != set(vector_ids):
        missing = len(set(chunk_ids) - set(vector_ids))
        extra = len(set(vector_ids) - set(chunk_ids))
        raise BundleBuildError(
            f"{missing:,} chunks have no vector and {extra:,} vectors have no chunk "
            f"({len(chunk_ids):,} chunks, {len(vector_ids):,} vectors) · is `ravel embed` "
            "finished?"
        )
    documents = set(_column(chunk_shards, "doc_id"))
    source_sha = hashlib.sha256("\n".join(sorted(documents)).encode()).hexdigest()

    first = next(iter_chunks(str(chunk_shards[0])))
    profile_ref = corpus.extract.profile or ""
    profile = registry.get(profile_ref) if profile_ref else None
    manifest = BundleManifest(
        corpus_id=corpus.id,
        run_id=run_id or chunk_dir.name,
        chunk_count=len(chunk_ids),
        source_manifest_sha256=source_sha,
        dense=spec_of(corpus.embed),
        sparse=None,
        chunker=first.chunker,
        chunker_version=first.chunker_version,
        profile_ref=profile_ref,
        scoring_vocabulary=profile.spec.scoring_vocabulary if profile else None,
        text_search_config=text_search_config,
        notes=notes,
    )
    writer = BundleWriter(root=Path(root), manifest=manifest)
    report = BuildReport(
        root=writer.root,
        chunks=len(chunk_ids),
        vectors=len(vector_ids),
        documents=len(documents),
    )

    # -- shards: copied, not re-encoded, so the bytes that were checked are the bytes shipped
    for kind, shards in (("chunks", chunk_shards), ("vectors", vector_shards)):
        for index, shard in enumerate(shards):
            target = writer.shard_path(kind, index)
            shutil.copyfile(shard, target)
            writer.record(
                f"{kind}/{target.name}", int(pq.ParquetFile(str(target)).metadata.num_rows)
            )

    # -- sidecars, restricted to documents the corpus holds -----------------------------
    def keep(
        rows: list[dict[str, Any]], key: str, name: str, *, unique: bool
    ) -> list[dict[str, Any]]:
        """Rows for documents the corpus holds; one per document when `unique`."""
        seen: set[str] = set()
        kept: list[dict[str, Any]] = []
        for row in rows:
            doc = row[key]
            if doc not in documents or (unique and doc in seen):
                continue
            seen.add(doc)
            kept.append(row)
        report.dropped[name] = len(rows) - len(kept)
        return kept

    if relations is not None:
        rows = keep(_rows(relations), "source_doc_id", "relations", unique=False)
        report.sidecars["relations"] = write_relations(writer.root / "relations.parquet", rows)
        writer.record("relations.parquet", report.sidecars["relations"])
    if status is not None:
        rows = keep(_rows(status), "doc_id", "doc_status", unique=True)
        report.sidecars["doc_status"] = write_doc_status(
            writer.root / "doc_status.parquet", rows
        )
        writer.record("doc_status.parquet", report.sidecars["doc_status"])
    if quality is not None:
        rows = keep(_rows(quality), "doc_id", "doc_quality", unique=True)
        report.sidecars["doc_quality"] = write_doc_quality(
            writer.root / "doc_quality.parquet", rows
        )
        writer.record("doc_quality.parquet", report.sidecars["doc_quality"])

    # -- what did not make it, from the ledger that recorded it -------------------------
    ledger_path = workspace / "state" / corpus.id / "ledger.db"
    entries: list[dict[str, Any]] = []
    if ledger_path.exists():
        with Ledger(ledger_path) as ledger:
            for stage in ("extract", f"chunk:{variant}"):
                for entry in ledger.failures(stage):
                    entries.append(
                        {
                            "stage": stage,
                            "doc_id": entry.doc_id,
                            "status": entry.status.value,
                            "detail": entry.detail,
                        }
                    )
    report.failed = writer.write_failed(entries)

    if reference is not None:
        writer.write_reference(reference[0], reference[1])
        report.reference = True

    writer.seal()
    report.manifest_sha256 = str(
        json.loads((writer.root / "manifest.json").read_text("utf-8"))["manifest_sha256"]
    )
    return report
