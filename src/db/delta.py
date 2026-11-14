"""A change to a loaded corpus, as a bundle-shaped directory: what to remove, what to add.

Documents are content-addressed: `doc_id` is the sha256 of the source bytes. So a changed
source is a new document and the old one is gone, an added source is a new `doc_id`, and a
removed source is a `doc_id` that is no longer there. A delta is therefore the difference of
two sealed bundles' document sets, and nothing about *how* to rebuild is decided here: the
new bundle comes from the ordinary pipeline (extraction and embeddings are cached by content
hash, so only the new documents cost anything), and this derives what changed from the two.

What carries across, and what does not:

* **Chunks, vectors, sparse vectors** of added documents are copied from the new bundle, not
  re-encoded.
* **Derived sidecars are replaced where they differ, not only for added documents.** A
  relation row of an *unchanged* document can change because a document it cites arrived or
  left (`target_doc_id` resolves, or stops resolving), and a status derived from relations
  changes with them. So the delta compares the sidecars of the two bundles and names every
  document whose rows differ.
* **The recipe must be identical.** A different embedder, chunker, profile or BM25
  vocabulary makes the old rows and the new ones different spaces; mixing them is the
  defect `CLAUDE.md` §7.3 forbids, so a delta between different recipes is refused and the
  answer is a full rebuild.

The directory is bundle-shaped (shards, sidecars, a checksummed inventory) plus
`scope.parquet` and a `delta` block in `manifest.json`, so the loader's checksum preflight and
its staging and merge steps are the ones used.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from bundle.writer import (
    BundleLayoutError,
    read_bundle,
    sha256_file,
    write_doc_quality,
    write_doc_status,
    write_relations,
)
from load.plan import RELATION_COLUMNS, STATUS_COLUMNS

__all__ = ["DeltaError", "DeltaReport", "build_delta", "recipe_differences"]

DELTA_VERSION = 1


class DeltaError(ValueError):
    """The two bundles cannot be turned into a delta. What it says is why."""


@dataclass
class DeltaReport:
    out: Path
    parent: str
    child: str
    added_docs: int = 0
    removed_docs: int = 0
    replaced_docs: int = 0
    unchanged_docs: int = 0
    added_chunks: int = 0
    removed_chunks: int = 0
    reset_status: int = 0
    reset_relations: int = 0

    @property
    def empty(self) -> bool:
        return not (
            self.added_docs or self.removed_docs or self.reset_status or self.reset_relations
        )


def recipe_differences(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """What differs between two manifests in a way that makes their vectors incomparable."""
    found: list[str] = []
    if old["corpus_id"] != new["corpus_id"]:
        found.append(f"corpus_id {old['corpus_id']!r} vs {new['corpus_id']!r}")
    if old["dense"] != new["dense"]:
        found.append("the dense embedder (model, revision, pooling, instruction or dtype)")
    sparse_keys = ("scheme", "dim", "k1", "b", "vocab_sha256")
    old_sparse = {k: (old.get("sparse") or {}).get(k) for k in sparse_keys}
    new_sparse = {k: (new.get("sparse") or {}).get(k) for k in sparse_keys}
    if old_sparse != new_sparse:
        found.append("the BM25 vocabulary (a refit changes every sparse vector)")
    for key in ("chunker", "chunker_version", "profile_ref", "text_search_config"):
        if old.get(key) != new.get(key):
            found.append(f"{key} {old.get(key)!r} vs {new.get(key)!r}")
    if old.get("scoring_vocabulary") != new.get("scoring_vocabulary"):
        found.append("the profile's scoring vocabulary")
    if old.get("omni") != new.get("omni"):
        found.append("the second (omni) vector space")
    return found


def _chunk_shards(root: Path, document: dict[str, Any], prefix: str) -> list[Path]:
    counts: dict[str, int] = document["inventory"]["counts"]
    return [root / name for name in sorted(counts) if name.startswith(prefix)]


def _documents(root: Path, document: dict[str, Any]) -> dict[str, tuple[str, int]]:
    """`doc_id -> (source_url, chunk count)` for every document in a bundle."""
    found: dict[str, tuple[str, int]] = {}
    for shard in _chunk_shards(root, document, "chunks/"):
        table = pq.read_table(str(shard), columns=["doc_id", "source_url"])
        for doc, url in zip(
            table.column("doc_id").to_pylist(),
            table.column("source_url").to_pylist(),
            strict=True,
        ):
            known = found.get(doc)
            found[doc] = (url if known is None else known[0], (known[1] if known else 0) + 1)
    return found


def _rows(path: Path) -> list[dict[str, Any]]:
    return list(pq.read_table(str(path)).to_pylist()) if path.exists() else []


def _freeze(row: dict[str, Any], columns: tuple[str, ...]) -> tuple[Any, ...]:
    return tuple(tuple(v) if isinstance(v, list) else v for v in (row[c] for c in columns))


def _group(
    rows: list[dict[str, Any]], key: str, columns: tuple[str, ...]
) -> dict[str, tuple[Any, ...]]:
    grouped: dict[str, list[tuple[Any, ...]]] = {}
    for row in rows:
        grouped.setdefault(row[key], []).append(_freeze(row, columns))
    return {k: tuple(sorted(v, key=repr)) for k, v in grouped.items()}


def _filter_shards(
    shards: list[Path], column: str, keep: pa.Array, out_dir: Path, *, collect: str
) -> tuple[dict[str, int], pa.Array]:
    """Copy rows whose `column` is in `keep` to `out_dir`; their `collect` values come back."""
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    kept: list[pa.Array] = []
    for shard in shards:
        table = pq.read_table(str(shard))
        mask = pc.is_in(table.column(column), value_set=keep)
        subset = table.filter(mask)
        if subset.num_rows == 0:
            continue
        name = f"{out_dir.name}/part-{len(counts):05d}.parquet"
        pq.write_table(subset, str(out_dir.parent / name), compression="zstd")
        counts[name] = subset.num_rows
        kept.append(subset.column(collect).combine_chunks())
    values = pa.concat_arrays(kept) if kept else pa.array([], pa.string())
    return counts, values


def build_delta(old: Path | str, new: Path | str, out: Path | str) -> DeltaReport:
    """The delta that takes a database at bundle `old` to bundle `new`."""
    old_root, new_root, target = Path(old), Path(new), Path(out)
    old_doc, new_doc = read_bundle(old_root), read_bundle(new_root)
    differences = recipe_differences(old_doc, new_doc)
    if differences:
        raise DeltaError(
            "these bundles were not built by the same recipe, so their vectors are not "
            "comparable and the answer is a full rebuild: " + "; ".join(differences)
        )
    if old_doc["manifest_sha256"] == new_doc["manifest_sha256"]:
        raise DeltaError("the two bundles are the same version: there is nothing to apply")
    if target.exists() and any(target.iterdir()):
        raise BundleLayoutError(f"{target} is not empty: a delta is written to a new directory")
    target.mkdir(parents=True, exist_ok=True)

    old_docs, new_docs = _documents(old_root, old_doc), _documents(new_root, new_doc)
    added = sorted(set(new_docs) - set(old_docs))
    removed = sorted(set(old_docs) - set(new_docs))
    added_urls = {new_docs[d][0] for d in added}
    report = DeltaReport(
        out=target,
        parent=str(old_doc["manifest_sha256"]),
        child=str(new_doc["manifest_sha256"]),
        added_docs=len(added),
        removed_docs=len(removed),
        replaced_docs=sum(1 for d in removed if old_docs[d][0] in added_urls),
        unchanged_docs=len(set(old_docs) & set(new_docs)),
        removed_chunks=sum(old_docs[d][1] for d in removed),
    )

    counts: dict[str, int] = {}
    added_set = pa.array(added, pa.string())
    chunk_counts, added_chunk_ids = _filter_shards(
        _chunk_shards(new_root, new_doc, "chunks/"),
        "doc_id",
        added_set,
        target / "chunks",
        collect="id",
    )
    counts.update(chunk_counts)
    report.added_chunks = len(added_chunk_ids)
    for prefix, column in (("vectors/", "chunk_id"), ("sparse/", "chunk_id")):
        shards = _chunk_shards(new_root, new_doc, prefix)
        if shards:
            shard_counts, _ = _filter_shards(
                shards, column, added_chunk_ids, target / prefix.rstrip("/"), collect=column
            )
            counts.update(shard_counts)

    # -- derived sidecars: every document whose rows differ, not only the added ones ------
    removed_set = set(removed)
    old_status = _group(_rows(old_root / "doc_status.parquet"), "doc_id", STATUS_COLUMNS)
    new_status_rows = _rows(new_root / "doc_status.parquet")
    new_status = _group(new_status_rows, "doc_id", STATUS_COLUMNS)
    reset_status = {
        d
        for d in old_status.keys() | new_status.keys()
        if d in removed_set or old_status.get(d) != new_status.get(d)
    }
    old_rel = _group(_rows(old_root / "relations.parquet"), "source_doc_id", RELATION_COLUMNS)
    new_rel_rows = _rows(new_root / "relations.parquet")
    new_rel = _group(new_rel_rows, "source_doc_id", RELATION_COLUMNS)
    reset_relations = {
        d
        for d in old_rel.keys() | new_rel.keys()
        if d in removed_set or old_rel.get(d) != new_rel.get(d)
    }
    report.reset_status, report.reset_relations = len(reset_status), len(reset_relations)

    if new_status_rows:
        counts["doc_status.parquet"] = write_doc_status(
            target / "doc_status.parquet",
            [r for r in new_status_rows if r["doc_id"] in reset_status],
        )
    if new_rel_rows:
        counts["relations.parquet"] = write_relations(
            target / "relations.parquet",
            [r for r in new_rel_rows if r["source_doc_id"] in reset_relations],
        )
    quality_rows = _rows(new_root / "doc_quality.parquet")
    if quality_rows:
        added_lookup = set(added)
        counts["doc_quality.parquet"] = write_doc_quality(
            target / "doc_quality.parquet",
            [r for r in quality_rows if r["doc_id"] in added_lookup],
        )

    scope = (
        [("removed", d) for d in removed]
        + [("status", d) for d in sorted(reset_status)]
        + [("relations", d) for d in sorted(reset_relations)]
    )
    pq.write_table(
        pa.table(
            {
                "kind": pa.array([k for k, _ in scope], pa.string()),
                "doc_id": pa.array([d for _, d in scope], pa.string()),
            }
        ),
        str(target / "scope.parquet"),
        compression="zstd",
    )
    counts["scope.parquet"] = len(scope)

    delta_block = {
        "version": DELTA_VERSION,
        "parent_manifest_sha256": report.parent,
        "child_manifest_sha256": report.child,
        "child_source_manifest_sha256": new_doc["source_manifest_sha256"],
        "child_chunk_count": int(new_doc["chunk_count"]),
        "added_docs": report.added_docs,
        "removed_docs": report.removed_docs,
        "replaced_docs": report.replaced_docs,
        "unchanged_docs": report.unchanged_docs,
        "added_chunks": report.added_chunks,
        "removed_chunks": report.removed_chunks,
        "reset_status": report.reset_status,
        "reset_relations": report.reset_relations,
    }
    checksums = {name: sha256_file(target / name) for name in sorted(counts)}
    document = {
        **{
            k: v
            for k, v in new_doc.items()
            if k not in ("inventory", "manifest_sha256", "clustering", "notes", "chunk_count")
        },
        "chunk_count": report.added_chunks,
        "clustering": {},
        "notes": f"delta {report.parent[:12]} -> {report.child[:12]}",
        "delta": delta_block,
        "inventory": {"counts": dict(sorted(counts.items())), "checksums": checksums},
    }
    document["manifest_sha256"] = hashlib.sha256(
        json.dumps({"delta": delta_block, "checksums": checksums}, sort_keys=True).encode()
    ).hexdigest()
    (target / "manifest.json").write_text(
        json.dumps(document, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    return report
