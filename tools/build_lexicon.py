"""Build the word list the quality gate counts against (`docs/QUALITY.md`).

    uv run python tools/build_lexicon.py work/id_reg_markdown \
        --out registry/lexicons/id_regulation_md@1.txt --min-docs 25 --workers 12

A word is in the list when it appears in at least `--min-docs` different documents.
Document frequency, not total frequency: `Pasal` repeated four thousand times in one
regulation says nothing about whether it is a word.

! Two passes, because one is not enough. A scanner batch makes the same mistake in every
document it touches, so `pcmerintah` and `scbagairnana` each appear in 25+ files and pass a
plain frequency cut. Pass 1 builds a provisional list from every document; pass 2 counts
again over only the documents that read as clean against it (`--clean`, the largest share of
unknown words a document may have), where a batch's typos are rare again.

The list is data in the registry, reviewed like a profile. Rebuilding it from a different
corpus is a new file, not an edit.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

TOKEN = re.compile(r"[^\W\d_]+", re.UNICODE)


_PROVISIONAL: frozenset[str] = frozenset()
_CLEAN = 1.0


def _init(provisional: frozenset[str], clean: float) -> None:
    global _PROVISIONAL, _CLEAN
    _PROVISIONAL, _CLEAN = provisional, clean


def _documents(paths: list[str]) -> Counter[str]:
    """Document frequency over these files; with a provisional list, only clean ones count."""
    seen: Counter[str] = Counter()
    for path in paths:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        words = [w.lower() for w in TOKEN.findall(text) if len(w) >= 3]
        if _PROVISIONAL and words:
            unknown = sum(1 for w in words if w not in _PROVISIONAL)
            if unknown / len(words) > _CLEAN:
                continue
        seen.update(set(words))
    return seen


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    ap.add_argument("root")
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-docs", type=int, default=25)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--clean", type=float, default=0.02)
    args = ap.parse_args()

    started = time.perf_counter()
    files = sorted(str(p) for p in Path(args.root).rglob("*.md") if p.stat().st_size)
    shards = [files[i :: args.workers * 4] for i in range(args.workers * 4)]
    frequency: Counter[str] = Counter()
    with ProcessPoolExecutor(args.workers) as pool:
        for part in pool.map(_documents, shards):
            frequency.update(part)
    provisional = frozenset(w for w, n in frequency.items() if n >= args.min_docs)
    print(f"pass 1: provisional list {len(provisional):,} words", flush=True)

    frequency = Counter()
    with ProcessPoolExecutor(
        args.workers, initializer=_init, initargs=(provisional, args.clean)
    ) as pool:
        for part in pool.map(_documents, shards):
            frequency.update(part)
    words = sorted(w for w, n in frequency.items() if n >= args.min_docs)
    body = "\n".join(words) + "\n"
    header = (
        f"# words used in at least {args.min_docs} of {len(files):,} documents\n"
        f"# source: {Path(args.root).name} · tokens: letters only, 3+ characters, lowercase\n"
        f"# {len(words):,} words from a vocabulary of {len(frequency):,}\n"
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(header + body, encoding="utf-8", newline="\n")
    print(
        f"{len(files):,} files · vocabulary {len(frequency):,} · kept {len(words):,} "
        f"· sha256 {hashlib.sha256((header + body).encode()).hexdigest()[:12]} "
        f"· {time.perf_counter() - started:.0f}s"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
