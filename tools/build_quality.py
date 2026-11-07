"""Judge every document of a Markdown corpus: is it worth indexing? (`docs/QUALITY.md`)

    uv run python tools/build_quality.py id_regulation_md@1.2 work/id_reg_markdown \
        --out work/quality --workers 16 [--every N] [--show 4] [--registry DIR]

Runs the layers up to chunking (the sealed knowledge base), then scores each document's
chunks with the profile's `quality:` section. Writes `doc_quality.parquet` -- a sidecar, one
row per document, disposable -- and prints what a person needs to decide whether the
thresholds are right: the verdict split, the reasons, and a sample of each verdict's text.

! Read the samples. A threshold that sorts well on counts can still be throwing away
documents a reader would have used, and only the text shows that.
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

from bundle import write_doc_quality  # noqa: E402
from chunk import Builder, get  # noqa: E402
from enrich.quality import DocQuality, QualityGate  # noqa: E402
from extract.structure import Structurer  # noqa: E402
from extract.text import TextExtractor  # noqa: E402
from spec import default_registry  # noqa: E402
from spec.corpus import ChunkSpec  # noqa: E402


def _chunk_config(profile: Any) -> Any:
    """The corpus's own chunk settings when `RAVEL_CORPUS` names one, else the defaults.

    ! A sidecar names chunks by id, and the id hashes the chunker config. A tool that chunks
    with different settings than `ravel chunk` writes evidence ids no chunk in the corpus has.
    """
    import os

    from spec.corpus import CorpusSpec

    corpus = os.environ.get("RAVEL_CORPUS")
    if corpus:
        return CorpusSpec.find(corpus).chunk.to_config(profile)
    return ChunkSpec().to_config(profile)


_STATE: dict[str, Any] = {}


def _one(args: tuple[str, str, str | None]) -> tuple[str, DocQuality | str, str]:
    ref, path_text, registry = args
    if _STATE.get("ref") != ref:
        profile = default_registry(registry).get(ref)
        _STATE.update(
            ref=ref,
            profile=profile,
            config=_chunk_config(profile),
            entry=get(profile.spec.pipeline.chunk.chunker or "unit"),
            gate=QualityGate(profile, registry),
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
        return path.name, f"{type(exc).__name__}: {exc}"[:200], ""
    quality = _STATE["gate"].score(doc.doc_id, (c.body for c in chunks))
    return path.name, quality, "\n".join(c.body for c in chunks[:3])[:300]


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    ap.add_argument("profile")
    ap.add_argument("root")
    ap.add_argument("--out", default="work/quality")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--every", type=int, default=1)
    ap.add_argument("--show", type=int, default=4)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--registry", default=None)
    args = ap.parse_args()

    files = sorted(p for p in Path(args.root).rglob("*.md") if p.stat().st_size)[:: args.every]
    started = time.perf_counter()
    jobs = [(args.profile, str(f), args.registry) for f in files]
    with ProcessPoolExecutor(args.workers) as pool:
        results = list(pool.map(_one, jobs, chunksize=16))

    errors = [(n, r) for n, r, _ in results if isinstance(r, str)]
    scored = [(n, r, text) for n, r, text in results if isinstance(r, DocQuality)]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    write_doc_quality(
        out / "doc_quality.parquet",
        (
            {
                "doc_id": q.doc_id,
                "verdict": q.verdict,
                "indexable": q.indexable,
                "chunks": q.chunks,
                "damaged_chunks": q.damaged_chunks,
                "words": q.words,
                "unknown_share": q.unknown_share,
                "damaged_share": q.damaged_share,
                "replacement_per_1000": q.replacement_per_1000,
                "reasons": list(q.reasons),
            }
            for _, q, _ in scored
        ),
    )

    gate = QualityGate(default_registry(args.registry).get(args.profile), args.registry)
    print(
        f"\n{args.profile} · {len(files):,} files · {len(errors)} failed · "
        f"{time.perf_counter() - started:.0f}s · lexicon {len(gate.lexicon):,} words "
        f"· gate {gate.hash}"
    )
    verdicts = Counter(q.verdict for _, q, _ in scored)
    print("\nVERDICT")
    for name in ("clean", "degraded", "unusable", "absent"):
        n = verdicts[name]
        print(f"  {name:9s} {n:>8,}  ({100 * n / max(len(scored), 1):.2f}%)")
    print(f"  indexable {sum(q.indexable for _, q, _ in scored):>8,}")

    damaged = sum(q.damaged_chunks for _, q, _ in scored)
    chunks = sum(q.chunks for _, q, _ in scored)
    print(
        f"\ndamaged chunks: {damaged:,} of {chunks:,} ({100 * damaged / max(chunks, 1):.2f}%)"
    )
    hit = [q for _, q, _ in scored if q.damaged_chunks]
    print(
        f"documents with any damaged chunk: {len(hit):,}; "
        f"of those, usable: {sum(q.verdict != 'unusable' for q in hit):,}"
    )

    rng = random.Random(args.seed)
    for name in ("degraded", "unusable", "absent"):
        group = [(fname, q, t) for fname, q, t in scored if q.verdict == name]
        print(f"\n== {name}  ({len(group):,})")
        for fname, q, text in rng.sample(group, min(args.show, len(group))):
            print(
                f"  [{fname[:44]}] chunks={q.chunks} words={q.words} "
                f"unknown={q.unknown_share:.0%} damaged={q.damaged_chunks} :: "
                f"{'; '.join(q.reasons)}"
            )
            print("      " + text[:200].replace("\n", " | "))
    for name, why in errors[:5]:
        print("ERROR", name, why)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
