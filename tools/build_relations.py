"""Build the relation sidecars for a Markdown corpus and measure them.

    uv run python tools/build_relations.py id_regulation_md@1.2 work/id_reg_markdown \
        --out work/relations --workers 16 [--every N] [--show 6]

Runs the layers up to chunking (the sealed knowledge base), reads each document's relations
from its chunks, then -- once every document is known -- resolves each cited instrument to a
document in the corpus and derives each document's status from the edges that point at it.

Writes two sidecars, both disposable (`docs/RELATIONS.md`):

* `relations.parquet`  one row per (document, relation, target), with the sentence it was read
  from and the chunk that holds it.
* `doc_status.parquet` one row per document: `revoked`, `partially_revoked`, `amended` or
  `none_known` -- never "in force" -- and who says so.

What it measures is whether the edges are right, not that they exist: the share of citations
that resolve, the share that are ambiguous, and a random sample of every kind of edge printed
with its sentence, for a person to read.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bundle import write_doc_status, write_relations  # noqa: E402
from chunk import Builder, get  # noqa: E402
from enrich.relations import (  # noqa: E402
    DocIdentity,
    Index,
    Relation,
    RelationExtractor,
    derive_status,
    identity_of,
    resolve,
)
from extract.structure import Structurer  # noqa: E402
from extract.text import TextExtractor  # noqa: E402
from spec import default_registry  # noqa: E402
from spec.corpus import ChunkSpec  # noqa: E402

_STATE: dict[str, Any] = {}


def _one(args: tuple[str, str]) -> dict[str, Any]:
    ref, path_text = args
    if _STATE.get("ref") != ref:
        profile = default_registry().get(ref)
        _STATE.update(
            ref=ref,
            profile=profile,
            config=ChunkSpec().to_config(profile),
            entry=get(profile.spec.pipeline.chunk.chunker or "unit"),
            extractor=RelationExtractor(profile),
        )
    profile, entry = _STATE["profile"], _STATE["entry"]
    path = Path(path_text)
    try:
        doc = TextExtractor().extract(path, title=path.stem, url=f"file://{path.name}")
        doc = Structurer(profile).apply(doc)
        builder = Builder(
            doc=doc,
            chunker=entry.id,
            version=entry.version,
            config=_STATE["config"],
            profile=profile,
        )
        chunks = list(entry.fn(doc, builder))
    except Exception as exc:  # noqa: BLE001 - one document must not kill a build
        return {"name": path.name, "error": f"{type(exc).__name__}: {exc}"[:200]}
    identity = identity_of(profile, chunks)
    relations = _STATE["extractor"].extract(chunks, identity)
    return {
        "name": path.name,
        "doc_id": chunks[0].doc_id if chunks else None,
        "identity": identity,
        "relations": relations,
        "chunks": len(chunks),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    ap.add_argument("profile")
    ap.add_argument("root")
    ap.add_argument("--out", default="work/relations")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--every", type=int, default=1)
    ap.add_argument("--show", type=int, default=5)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    files = sorted(p for p in Path(args.root).rglob("*.md") if p.stat().st_size)[:: args.every]
    started = time.perf_counter()
    with ProcessPoolExecutor(args.workers) as pool:
        results = list(pool.map(_one, [(args.profile, str(f)) for f in files], chunksize=16))
    read = time.perf_counter()

    errors = [r for r in results if "error" in r]
    docs = [r for r in results if r.get("doc_id")]
    identities: list[DocIdentity] = [r["identity"] for r in docs if r["identity"] is not None]
    index = Index(identities)
    by_id = {i.doc_id: i for i in identities}

    rows: list[dict[str, Any]] = []
    edges: list[tuple[str, str, str | None, bool]] = []
    samples: dict[str, list[tuple[str, Relation, str, str]]] = {}
    resolution: Counter[tuple[str, str]] = Counter()
    for r in docs:
        citing = r["identity"]
        for rel in r["relations"]:
            target, how, confidence = resolve(rel, citing, index)
            resolution[(rel.relation, how)] += 1
            if how == "ambiguous":
                why = (
                    "citing has no identity"
                    if citing is None
                    else "citing has no region"
                    if not citing.enacting_body
                    else "citing has a region, several fit"
                )
                resolution[(rel.relation, "ambiguous:" + why)] += 1
            if how == "unresolved":
                peers = index.candidates(rel.target_number, rel.target_year)
                resolution[
                    (rel.relation, "unresolved:near" if peers else "unresolved:absent")
                ] += 1
                if peers:
                    samples.setdefault(f"{rel.relation}/unresolved-near", []).append(
                        (
                            r["name"],
                            rel,
                            how,
                            f"{rel.quote[:150]} || CANDIDATES "
                            + "; ".join(
                                f"{c.regulation_type} [{c.enacting_body}]" for c in peers[:4]
                            ),
                        )
                    )
            edges.append((r["doc_id"], rel.relation, target, rel.partial))
            rows.append(
                {
                    "source_doc_id": r["doc_id"],
                    "relation": rel.relation,
                    "target_text": rel.target_text,
                    "target_type": rel.target_type,
                    "target_number": rel.target_number,
                    "target_year": rel.target_year,
                    "target_doc_id": target,
                    "resolution": how,
                    "confidence": confidence,
                    "partial": rel.partial,
                    "mentions": rel.mentions,
                    "evidence_chunk_id": rel.evidence_chunk_id,
                    "quote": rel.quote,
                }
            )
            samples.setdefault(f"{rel.relation}/{how}", []).append(
                (r["name"], rel, how, rel.quote)
            )

    status = derive_status(edges, [d["doc_id"] for d in docs], index.copies)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    write_relations(out / "relations.parquet", rows)
    write_doc_status(
        out / "doc_status.parquet",
        (
            {
                "doc_id": st.doc_id,
                "status": st.status,
                "revoked_by": sorted(set(st.revoked_by)),
                "amended_by": sorted(set(st.amended_by)),
            }
            for st in status.values()
        ),
    )

    # ---- report -----------------------------------------------------------------------
    print(
        f"\n{args.profile} · {len(files):,} files · {len(errors)} failed · "
        f"read {read - started:.0f}s"
    )
    print(
        f"documents with an identity (can be cited): {len(identities):,} of {len(docs):,} "
        f"({100 * len(identities) / max(len(docs), 1):.1f}%)"
    )
    by_rel = Counter(row["relation"] for row in rows)
    print("\nEDGES")
    for relation, n in by_rel.most_common():
        total = sum(
            resolution[(relation, how)]
            for how in ("resolved", "inferred", "ambiguous", "unresolved")
        )
        parts = ", ".join(
            f"{how} {100 * resolution[(relation, how)] / total:.0f}%"
            for how in ("resolved", "inferred", "ambiguous", "unresolved")
            if resolution[(relation, how)]
        )
        docs_with = len({row["source_doc_id"] for row in rows if row["relation"] == relation})
        print(f"  {relation:11s} {n:>9,} edges in {docs_with:>6,} docs   {parts}")
    print("\nUNRESOLVED: no document has that number/year (not in corpus) vs some do (near)")
    for relation in by_rel:
        absent = resolution[(relation, "unresolved:absent")]
        near = resolution[(relation, "unresolved:near")]
        print(f"  {relation:11s} absent {absent:>8,}   near {near:>8,}")
    print("\nAMBIGUOUS, by why")
    for (relation, reason), n in sorted(resolution.items()):
        if reason.startswith("ambiguous:"):
            print(f"  {relation:11s} {reason[10:]:36s} {n:>8,}")
    counts = Counter(s.status for s in status.values())
    print("\nSTATUS (derived from resolved edges; absence of an edge is not validity)")
    for name, n in counts.most_common():
        print(f"  {name:18s} {n:>8,}  ({100 * n / max(len(status), 1):.1f}%)")
    cycles = sum(
        1
        for a, rel, b, _ in edges
        if rel in ("revokes", "replaces")
        and b
        and (b, rel, a, False)
        in {(x, y, z, False) for x, y, z, _ in edges if y == rel and z == a}
    )
    print(f"\nmutual revocations (A revokes B and B revokes A): {cycles // 2 if cycles else 0}")
    self_in_year = sum(
        1
        for a, rel, b, _ in edges
        if rel in ("revokes", "replaces")
        and b
        and a in by_id
        and b in by_id
        and by_id[b].year > by_id[a].year
    )
    print(f"revocations of a LATER-year document (impossible; a wrong link): {self_in_year}")
    for a, rel, b, _ in edges:
        if (
            rel in ("revokes", "replaces")
            and b
            and a in by_id
            and b in by_id
            and by_id[b].year > by_id[a].year
        ):
            print("   LATER:", by_id[a], "->", by_id[b])

    rng = random.Random(args.seed)
    print("\nSAMPLES (read these)")
    for key in sorted(samples):
        if (
            args.show
            and not key.endswith(("/resolved", "/inferred", "/ambiguous"))
            and not key.startswith(("revokes", "replaces", "amends"))
        ):
            continue
        chosen = rng.sample(samples[key], min(args.show, len(samples[key])))
        print(f"\n== {key}  ({len(samples[key]):,})")
        for name, rel, _, quote in chosen:
            print(
                f"  [{name[:34]}] -> {rel.target_text[:70]!r}"
                f"{' PARTIAL' if rel.partial else ''}"
            )
            print(f"      {quote[:260]}")
    for e in errors[:5]:
        print("ERROR", e)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
