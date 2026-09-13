"""Measure how well a pipeline recovers document structure.

Answers OPEN_QUESTIONS.md §4. The `id_regulation` chunker rests entirely on heading
detection being right; if `Pasal 9` is left as a paragraph, chunking on the legal
unit silently degrades and no amount of ranking work repairs it.

Two metrics, because one of them is circular
--------------------------------------------
**Oracle agreement** compares detected headings against
`chunk.id_patterns.structural_level`. Honest for the *typography* arm, which never
sees the patterns. Meaningless for the *pattern* arm, which is built from them —
reported anyway, and labelled, because a number that is 100% by construction is
still worth seeing stated.

**Sequence coherence** is the independent one. A regulation's pasal run 1, 2, 3…;
a structurer that hallucinates or drops headings produces gaps and repeats. Nothing
in either arm optimizes for this, so both arms can be compared on it fairly.

    python tools/structure_eval.py <uri-of-pdfs> [--limit 50]

`<uri>` is a URI, so the same command measures a local directory, a Hugging Face
dataset or an S3/R2 bucket without changing (`INTERFACES.md` §5):

    python tools/structure_eval.py sources/id_legal
    python tools/structure_eval.py hf://datasets/Azzindani/ID_REG --cache-dir .cache
    python tools/structure_eval.py s3://bucket/sources --limit 300

Remote documents are fetched one at a time and discarded, unless `--cache-dir` is
given, which turns the fetch into a cache a resumed run reuses.
"""

from __future__ import annotations

import argparse
import random
import re
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from canon import BlockType, CanonicalDoc  # noqa: E402
from extract import ExtractionFailed, NativeExtractor, probe  # noqa: E402
from extract.structure import Structurer  # noqa: E402
from spec import default_registry  # noqa: E402
from spec.models import Unit  # noqa: E402
from uris import as_local_path, find  # noqa: E402

PASAL_NUMBER = re.compile(r"^PASAL\s+(\d+)", re.I)


@dataclass
class Arm:
    """One detection strategy, scored on the same documents as every other."""

    name: str
    note: str
    oracle: Callable[[str], Unit | None]
    """! `Unit | None`, not `object | None`. The eval reads `truth.name` to break
    results down per structural unit, and a widened type makes that attribute
    unresolvable while the code keeps working — until a profile changes shape."""
    hits: int = 0
    misses: int = 0
    extra: int = 0
    docs: int = 0
    coherent: int = 0
    gaps: int = 0
    repeats: int = 0
    runs: int = 0
    by_unit: Counter[str] = field(default_factory=Counter)
    missed_by_unit: Counter[str] = field(default_factory=Counter)

    def score(self, doc: CanonicalDoc) -> None:
        self.docs += 1
        for block in doc.ordered():
            truth = self.oracle(block.text)
            predicted = block.type is BlockType.HEADING
            if truth:
                self.by_unit[truth.name] += 1
                if predicted:
                    self.hits += 1
                else:
                    self.misses += 1
                    self.missed_by_unit[truth.name] += 1
            elif predicted:
                self.extra += 1
        self._sequence(doc)

    def _sequence(self, doc: CanonicalDoc) -> None:
        """Independent check: do the detected pasal numbers form clean runs?

        ! An Indonesian regulation legitimately contains TWO pasal runs — the body,
        then the Penjelasan (elucidation), which restates Pasal 1..N to explain
        them. A single monotonic sequence is therefore the wrong expectation: a
        backwards step is a section boundary, not an error. Runs are split there and
        each is checked for gaps on its own.
        """
        numbers = [
            int(m.group(1))
            for b in doc.ordered()
            if b.type is BlockType.HEADING and (m := PASAL_NUMBER.match(b.text.strip()))
        ]
        if not numbers:
            return

        runs: list[list[int]] = [[numbers[0]]]
        for previous, current in zip(numbers, numbers[1:], strict=False):
            if current < previous:
                runs.append([current])  # a new section restarts the numbering
            else:
                runs[-1].append(current)

        gaps = repeats = 0
        for run in runs:
            for a, b in zip(run, run[1:], strict=False):
                gaps += b - a not in (0, 1)
                repeats += b == a
        self.gaps += gaps
        self.repeats += repeats
        self.runs += len(runs)
        if numbers[0] == 1 and not gaps and not repeats:
            self.coherent += 1

    def report(self) -> None:
        labelled = self.hits + self.misses
        recall = self.hits / labelled if labelled else 0.0
        precision = self.hits / (self.hits + self.extra) if self.hits + self.extra else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

        print(f"\n--- {self.name} ---   {self.note}")
        print(
            f"  oracle headings    {labelled:,}   "
            f"detected {self.hits:,}  missed {self.misses:,}"
        )
        print(f"  extra headings     {self.extra:,}")
        print(f"  recall             {recall:7.1%}")
        print(f"  precision          {precision:7.1%}")
        print(f"  F1                 {f1:7.1%}")
        print("  -- independent: pasal sequence coherence --")
        share = self.coherent / max(self.docs, 1)
        print(f"  clean 1..N runs    {self.coherent}/{self.docs}  ({share:.0%})")
        print(f"  gaps               {self.gaps:,}")
        print(f"  repeats            {self.repeats:,}")
        print(f"  sections (runs)    {self.runs:,}  over {self.docs} documents")
        if self.by_unit and self.hits:
            worst = sorted(
                ((u, n - self.missed_by_unit[u], n) for u, n in self.by_unit.items()),
                key=lambda t: t[1] / t[2],
            )[:4]
            print("  weakest units      " + "  ".join(f"{u} {f / t:.0%}" for u, f, t in worst))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("root", help="URI of the documents: a path, hf://, s3://, file://")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--profile", default="id_regulation")
    ap.add_argument(
        "--cache-dir",
        default=None,
        help="keep fetched remote documents here instead of discarding them",
    )
    args = ap.parse_args()

    # Case variants and ordering are both handled in `uris.find`, and both are
    # correctness issues rather than tidiness — see the notes there.
    fs, pdfs = find(args.root)
    random.Random(args.seed).shuffle(pdfs)
    pdfs = pdfs[: args.limit]
    if not pdfs:
        print(f"no PDFs under {args.root}", file=sys.stderr)
        return 2

    profile = default_registry().get(args.profile)
    structurer = Structurer(profile)
    extractor = NativeExtractor()
    oracle = profile.structural
    typography = Arm(
        "typography (native@1.0)", "font size + weight; never sees the patterns", oracle
    )
    pattern = Arm(
        f"pattern ({profile.ref})", "oracle columns are circular; read coherence", oracle
    )

    scanned = pages = failures = 0
    t0 = time.time()

    for entry in pdfs:
        # Local files yield in place and cost nothing; remote ones are fetched here and
        # released at the end of the block, so memory and disk stay bounded at one
        # document regardless of how large the corpus is.
        with as_local_path(fs, entry, cache_dir=args.cache_dir) as path:
            p = probe(path)
            pages += p.pages or 0
            if p.text_ratio < 0.9:
                scanned += 1
                continue
            try:
                doc = extractor.extract(path, title=path.stem)
            except ExtractionFailed:
                failures += 1
                continue
        typography.score(doc)
        pattern.score(structurer.apply(doc))

    print(f"\n=== structure evaluation · {len(pdfs)} documents ===")
    print(f"pages                {pages:,}   ({time.time() - t0:.1f}s)")
    print(f"no text layer        {scanned}  ({scanned / len(pdfs):.0%})  -> needs OCR/VLM")
    print(f"extraction failures  {failures}")

    typography.report()
    pattern.report()

    print(
        "\nRead: the pattern arm's recall is circular by construction. The comparison "
        "that counts is\npasal sequence coherence, which neither arm optimizes for."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
