"""Relabel a built bundle under a newer profile's text rules, without rebuilding it.

    ravel bundle relabel <bundle> --out <new bundle>

A profile that learns a label from a chunk's own text (`chunk.labels`) changes chunks that
are already built, embedded and loaded. Rebuilding would re-embed all 1.7M of them to change
a label on 2,860. This applies the rule to the bundle instead, and re-embeds only the chunks
whose *embedded text* changed: the profile's embed template carries `{chapter}`, so a new
chapter is a new input and its old vector would describe text the chunk no longer has.

What it guarantees, and how:

* **The result is what a rebuild would carry.** The rule reads `(chapter, article, body)`,
  which a built chunk has, and is the same function the chunker calls. Chunk ids do not
  move, because an id hashes the locator and the blocks, never the chapter.
* **The new vectors are in the bundle's space.** Before anything is replaced, the embedder
  re-embeds chunks the rule did not touch and must reproduce their stored vectors (cosine
  >= `REPRODUCE`). An embedder that cannot is refused, never mixed in (`CLAUDE.md` §7.3).
* **A published bundle is never edited.** The output is a new directory: unchanged files
  are hard links (copied where the filesystem cannot link), the shards holding a changed row
  are rewritten, and the inventory is resealed. `relabel.parquet` lists every changed id
  with its old and new chapter; `ravel db relabel` applies exactly that list.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from bundle.writer import BundleLayoutError, read_bundle, sha256_file
from chunk.derive import embed_text
from chunk.labels import ExplanationRule
from spec import Profile

__all__ = ["REPRODUCE", "RelabelError", "RelabelReport", "plan_relabel", "relabel_bundle"]

#: The cosine an unchanged chunk's re-embedding must reach against its stored vector. fp16
#: storage alone costs ~1e-4; anything lower is a different model, revision or recipe.
REPRODUCE = 0.999
#: Columns the embed template and the rule read.
_FIELDS = (
    "id",
    "chapter",
    "article",
    "body",
    "regulation_type",
    "regulation_number",
    "year",
    "about",
    "enacting_body",
    "identifier",
    "locator_section",
    "heading_path",
)

Embed = Callable[[list[str]], np.ndarray[Any, Any]]


class RelabelError(RuntimeError):
    pass


@dataclass
class RelabelReport:
    changed: int = 0
    shards: list[str] = field(default_factory=list)
    reproduced: float = 0.0
    """Lowest cosine of the reproduction check."""
    moved: float = 0.0
    """Mean cosine between a changed chunk's old vector and its new one."""
    out: Path | None = None
    parent: str = ""
    child: str = ""


def _rule(profile: Profile) -> ExplanationRule:
    spec = profile.spec.structure.explanation
    if spec is None:
        raise RelabelError(f"{profile.ref} declares no text rule for labels; nothing to apply")
    return ExplanationRule.compile(spec)


def _chunk_shards(root: Path) -> list[Path]:
    return sorted((root / "chunks").glob("part-*.parquet"))


def plan_relabel(root: Path, profile: Profile) -> dict[str, tuple[str | None, str]]:
    """`{chunk id: (old chapter, new chapter)}` for every chunk the profile relabels."""
    rule = _rule(profile)
    out: dict[str, tuple[str | None, str]] = {}
    for shard in _chunk_shards(root):
        t = pq.read_table(shard, columns=["id", "chapter", "article", "body"]).to_pydict()
        for cid, chapter, article, body in zip(
            t["id"], t["chapter"], t["article"], t["body"], strict=True
        ):
            new = rule.chapter_for(chapter, article, body or "")
            if new is not None:
                out[cid] = (chapter, new)
    return out


def _rows(root: Path, ids: set[str], limit: int | None = None) -> dict[str, dict[str, Any]]:
    """The chunk rows for `ids` (or, with `limit` and no ids, the first rows found)."""
    found: dict[str, dict[str, Any]] = {}
    for shard in _chunk_shards(root):
        have = set(pq.read_schema(shard).names)
        t = pq.read_table(shard, columns=[c for c in _FIELDS if c in have]).to_pylist()
        for row in t:
            if (row["id"] in ids) if ids else len(found) < (limit or 0):
                found[row["id"]] = row
        if limit is not None and len(found) >= limit:
            break
    return found


def _vectors(root: Path, ids: set[str]) -> dict[str, np.ndarray[Any, Any]]:
    out: dict[str, np.ndarray[Any, Any]] = {}
    for shard in sorted((root / "vectors").glob("part-*.parquet")):
        t = pq.read_table(shard)
        names = t.column("chunk_id").to_pylist()
        hit = [i for i, n in enumerate(names) if n in ids]
        if not hit:
            continue
        emb = t.column("embedding").take(pa.array(hit))
        flat = emb.combine_chunks().flatten().to_numpy(zero_copy_only=False)
        mat = flat.astype(np.float32).reshape(len(hit), -1)
        for k, i in enumerate(hit):
            out[names[i]] = mat[k]
    return out


def _texts(rows: Sequence[dict[str, Any]], profile: Profile) -> list[str]:
    return [embed_text(SimpleNamespace(**r), profile) for r in rows]  # type: ignore[arg-type]


def _cos(a: np.ndarray[Any, Any], b: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
    a = a / np.linalg.norm(a, axis=1, keepdims=True)
    b = b / np.linalg.norm(b, axis=1, keepdims=True)
    out: np.ndarray[Any, Any] = np.einsum("ij,ij->i", a, b)
    return out


def _link(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _rewrite(src: Path, dst: Path, table: pa.Table) -> None:
    meta = pq.ParquetFile(src).metadata
    codec = meta.row_group(0).column(0).compression.lower() if meta.num_row_groups else "zstd"
    dst.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, dst, compression=codec)


def relabel_bundle(
    root: Path | str,
    out: Path | str,
    profile: Profile,
    embed: Embed,
    *,
    check: int = 16,
    on_note: Callable[[str], None] | None = None,
) -> RelabelReport:
    """Write `out`: `root` under the profile's text rules, changed chunks re-embedded."""
    root, out = Path(root), Path(out)
    note = on_note or (lambda _m: None)
    document = read_bundle(root)
    if (out / "manifest.json").exists():
        raise BundleLayoutError(f"{out} is already a sealed bundle; a bundle is never edited")
    report = RelabelReport(parent=str(document["manifest_sha256"]))

    changes = plan_relabel(root, profile)
    report.changed = len(changes)
    note(f"{len(changes):,} chunks relabelled by {profile.ref}")
    if not changes:
        return report

    # 1 · the embedder must reproduce this bundle's space before it writes into it.
    sample = [r for r in _rows(root, set(), limit=check * 4).values() if r["id"] not in changes]
    sample = sample[:check]
    stored = _vectors(root, {r["id"] for r in sample})
    sample = [r for r in sample if r["id"] in stored]
    if not sample:
        raise RelabelError("no unchanged chunk with a stored vector to check the embedder on")
    got = embed(_texts(sample, profile))
    cos = _cos(got, np.stack([stored[r["id"]] for r in sample]))
    report.reproduced = float(cos.min())
    note(f"embedder reproduces {len(sample)} stored vectors, lowest cosine {cos.min():.5f}")
    if cos.min() < REPRODUCE:
        raise RelabelError(
            f"the embedder does not reproduce this bundle's vectors (lowest cosine "
            f"{cos.min():.4f} < {REPRODUCE}): a different model, revision or recipe. Refused "
            f"rather than mixing two spaces in one corpus."
        )

    # 2 · re-embed the changed chunks with their new label.
    rows = _rows(root, set(changes))
    order = sorted(rows)
    relabelled = [{**rows[i], "chapter": changes[i][1]} for i in order]
    fresh = embed(_texts(relabelled, profile))
    old = _vectors(root, set(order))
    report.moved = float(_cos(fresh, np.stack([old[i] for i in order])).mean())
    new_vec = dict(zip(order, fresh, strict=True))
    note(f"{len(order):,} re-embedded; mean cosine to their old vectors {report.moved:.4f}")

    # 3 · the new bundle: links for what did not change, rewrites for what did.
    out.mkdir(parents=True, exist_ok=True)
    checksums = dict(document["inventory"]["checksums"])
    counts = dict(document["inventory"]["counts"])
    rewritten: set[str] = set()
    for shard in _chunk_shards(root):
        rel = shard.relative_to(root).as_posix()
        t = pq.read_table(shard)
        ids = t.column("id").to_pylist()
        hit = [k for k, i in enumerate(ids) if i in changes]
        if not hit:
            continue
        chapter = t.column("chapter").to_pylist()
        prof = t.column("profile").to_pylist() if "profile" in t.schema.names else None
        for k in hit:
            chapter[k] = changes[ids[k]][1]
            if prof is not None:
                prof[k] = profile.ref
        t = t.set_column(
            t.schema.get_field_index("chapter"),
            "chapter",
            pa.array(chapter, type=t.schema.field("chapter").type),
        )
        if prof is not None:
            t = t.set_column(
                t.schema.get_field_index("profile"),
                "profile",
                pa.array(prof, type=t.schema.field("profile").type),
            )
        _rewrite(shard, out / rel, t)
        rewritten.add(rel)
    for shard in sorted((root / "vectors").glob("part-*.parquet")):
        rel = shard.relative_to(root).as_posix()
        t = pq.read_table(shard)
        names = t.column("chunk_id").to_pylist()
        hit = [k for k, n in enumerate(names) if n in new_vec]
        if not hit:
            continue
        field_t = t.schema.field("embedding").type
        dim = field_t.list_size
        flat = t.column("embedding").combine_chunks().flatten().to_numpy(zero_copy_only=False)
        mat = flat.reshape(len(names), dim).copy()
        for k in hit:
            mat[k] = new_vec[names[k]].astype(mat.dtype)
        emb = pa.FixedSizeListArray.from_arrays(pa.array(mat.reshape(-1)), dim)
        t = t.set_column(t.schema.get_field_index("embedding"), "embedding", emb.cast(field_t))
        _rewrite(shard, out / rel, t)
        rewritten.add(rel)
    for rel in checksums:
        if rel not in rewritten:
            _link(root / rel, out / rel)
    for rel in rewritten:
        checksums[rel] = sha256_file(out / rel)
    report.shards = sorted(rewritten)

    ids = sorted(changes)
    pq.write_table(
        pa.table(
            {
                "id": ids,
                "old_chapter": [changes[i][0] for i in ids],
                "new_chapter": [changes[i][1] for i in ids],
            }
        ),
        out / "relabel.parquet",
        compression="zstd",
    )
    checksums["relabel.parquet"] = sha256_file(out / "relabel.parquet")
    counts["relabel.parquet"] = len(ids)

    # 4 · the manifest: a new identity, its parent, and what was done.
    body = {k: v for k, v in document.items() if k != "manifest_sha256"}
    body["profile_ref"] = profile.ref
    body["relabel"] = {
        "parent_manifest_sha256": report.parent,
        "profile": profile.ref,
        "rule": profile.spec.structure.explanation.model_dump(),  # type: ignore[union-attr]
        "changed": len(ids),
        "reproduced_min_cosine": round(report.reproduced, 6),
        "mean_cosine_to_old": round(report.moved, 6),
        "at": datetime.now(UTC).isoformat(),
    }
    body["inventory"] = {
        "counts": dict(sorted(counts.items())),
        "checksums": dict(sorted(checksums.items())),
        "files": len(checksums),
    }
    # ! A new identity, ✗ the parent's: the database records which bundle it holds by this
    # hash, and two corpora with different labels must not share one. Same recipe rule as
    # `BundleManifest.fingerprint` (run, time and notes excluded), plus the relabel block.
    ident = {
        k: v
        for k, v in body.items()
        if k
        not in {
            "run_id",
            "created_at",
            "notes",
            "inventory",
            "bundle_version",
            "clustering",
            "provenance_complete",
        }
    }
    ident["relabel"] = {k: v for k, v in body["relabel"].items() if k != "at"}
    report.child = hashlib.sha256(
        json.dumps(ident, sort_keys=True, default=str, ensure_ascii=False).encode()
    ).hexdigest()
    (out / "manifest.json").write_text(
        json.dumps(
            {
                "bundle_version": body.pop("bundle_version", "1"),
                "manifest_sha256": report.child,
                **body,
            },
            indent=2,
            default=str,
            ensure_ascii=False,
        ),
        encoding="utf-8",
        newline="\n",
    )
    report.out = out
    return report
