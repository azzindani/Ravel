"""Measure a profile against a directory of Markdown regulations · three read-only reports.

    python tools/probe_markdown_corpus.py structure id_regulation_md@1.1 work/id_reg_markdown
    python tools/probe_markdown_corpus.py cite      id_regulation_md@1.1 work/id_reg_markdown
    python tools/probe_markdown_corpus.py cleanup   id_regulation_md@1.1 work/md --every 24

`<profile>` is a ref in the registry or a path to a `<id>@<version>.yaml`; `--every N` reads
every Nth file in sorted order, so a run is a deterministic sample rather than a lottery.
Everything is read through the SAME Markdown extractor the build uses
(`extract.text._markdown_blocks`), so what is measured is what a profile would see: the
bullet and the `1.` list number are already stripped, `##` is already a heading block.

structure   How many lines each unit claims, and the lines that LOOK like a marker but were
            not claimed, grouped by shape (digits -> N, roman numerals -> R). The misses are
            the point: a profile is wrong silently, and a loose detector is the only way to
            see what it never matched.
cite        How often `profile.cite()` finds an identifier in the chunker's citation window,
            and how often its number agrees with the one the FILE NAME carries. File names
            are not ground truth (an amendment is named for what it amends), so read the
            agreement as a lower bound and the disagreements as a list to look at.
cleanup     What each `cleanup.boilerplate` pattern removes, with random distinct examples.
            Read the examples: a pattern that works on its first ten hits can still delete a
            clause on the eleventh.

facts       What `profile.describe()` reads from each file -- type, issuing body, number, year,
            subject, date -- as coverage, and the most common values. A field that is
            missing is None, never `Unknown`, so the gap is a number.
labels      The `chapter` and `article` labels the unit chunker stamps on chunks, by shape
            (digits -> N, roman numerals -> R). Runs the real extractor, structurer and
            chunker, so it is the slowest report: sample with `--every`.

! Nothing here writes. It reads files under the directory you name and prints.
"""

from __future__ import annotations

import argparse
import collections
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chunk import Builder, ChunkConfig, get  # noqa: E402
from chunk.unit import CITATION_BLOCKS, FACTS_BLOCKS  # noqa: E402
from extract.base import BlockBuilder  # noqa: E402
from extract.structure import Structurer  # noqa: E402
from extract.text import TextExtractor, _markdown_blocks  # noqa: E402
from spec import Profile, default_registry  # noqa: E402
from spec.loader import load_spec  # noqa: E402

#: Deliberately LOOSE (OCR variants included): these exist to find what a profile missed.
LOOSE: dict[str, re.Pattern[str]] = {
    "pasal": re.compile(
        r"^\W*(pasal|pasai|pasa1|pasan|pasel|pasa)\s*[0-9IlOo]+[A-Za-z]?\b", re.I
    ),
    "bab": re.compile(r"^\W*b\s?a\s?b\s*[IVXLC1l]+\b", re.I),
    "bagian": re.compile(r"^\W*bagian\s+\w+", re.I),
    "paragraf": re.compile(r"^\W*paragraf\s+\w+", re.I),
    "menimbang": re.compile(r"^\W*menimbang\b", re.I),
    "mengingat": re.compile(r"^\W*mengingat\b", re.I),
    "memutuskan": re.compile(r"^\W*memutuskan\b", re.I),
    "menetapkan": re.compile(r"^\W*menetapkan\b", re.I),
    "penjelasan": re.compile(r"^\W*penjelasan\b"),
    "lampiran": re.compile(r"^\W*LAMPIRAN\b"),
}

#: Blocks `facts` reads as the title block; `--window` changes it. A list so main() can set it.
WINDOW = [FACTS_BLOCKS]

#: What a file name says about its own number and year. Used only to cross-check `cite`.
NUMBER = re.compile(r"(?:nomor|no)[._\-\s]*0*(\d{1,4})", re.I)


def load_profile(ref: str) -> Profile:
    if ref.endswith(".yaml"):
        return Profile.compile(load_spec(Path(ref)))
    return default_registry().get(ref)


def sample_files(root: Path, every: int) -> list[Path]:
    files = sorted(p for p in root.rglob("*.md") if p.stat().st_size > 0)
    return files[:: max(1, every)]


def block_texts(path: Path) -> list[str]:
    builder = BlockBuilder()
    _markdown_blocks(path.read_text(encoding="utf-8", errors="replace"), builder)
    return [block.text for block in builder.blocks]


def shape(text: str, width: int = 44) -> str:
    text = re.sub(r"\d+", "N", text[:width])
    return re.sub(r"\b[IVXLC]{1,6}\b", "R", text)


def report_structure(profile: Profile, files: list[Path], *, top: int) -> None:
    claimed: collections.Counter[str] = collections.Counter()
    loose: collections.Counter[str] = collections.Counter()
    missed: dict[str, collections.Counter[tuple[str | None, str]]] = collections.defaultdict(
        collections.Counter
    )
    blocks = 0
    for path in files:
        for text in block_texts(path):
            blocks += 1
            unit = profile.structural(text)
            if unit:
                claimed[unit.name] += 1
            for name, pattern in LOOSE.items():
                if pattern.match(text):
                    loose[name] += 1
                    if unit is None or unit.name != name:
                        missed[name][(unit.name if unit else None, shape(text))] += 1
    print(f"{profile.ref}: files={len(files)} blocks={blocks}")
    print("claimed:", dict(claimed.most_common()))
    for name in LOOSE:
        count = sum(missed[name].values())
        print(f"\n== {name}: {loose[name]} loose lines, {count} not claimed as '{name}'")
        for (other, text), n in missed[name].most_common(top):
            print(f"   {n:5d}  -> {other}   {text!r}")


def expected_number(name: str) -> str | None:
    found = NUMBER.search(name)
    return found.group(1) if found else None


def report_cite(profile: Profile, files: list[Path], *, top: int) -> None:
    cited = comparable = agree = 0
    disagreements: list[tuple[str, str]] = []
    for path in files:
        text = "\n".join(block_texts(path)[:CITATION_BLOCKS])
        citation = profile.cite(text)
        if not citation:
            continue
        cited += 1
        expected = expected_number(path.name)
        number = citation.rsplit(" ", 1)[-1].split("/")[0]
        if expected is None:
            continue
        comparable += 1
        if number.lstrip("0") == expected.lstrip("0"):
            agree += 1
        else:
            disagreements.append((path.name[:56], citation))
    share = 100 * cited / max(len(files), 1)
    print(f"{profile.ref}: files={len(files)} cited={cited} ({share:.1f}%)")
    print(f"number agrees with the file name: {agree}/{comparable}")
    for item in disagreements[:top]:
        print("   DISAGREE", item)


def report_facts(profile: Profile, files: list[Path], *, top: int) -> None:
    counts: dict[str, collections.Counter[str]] = {
        name: collections.Counter()
        for name in ("regulation_type", "enacting_body", "year", "effective_date")
    }
    present = collections.Counter[str]()
    agree = comparable = 0
    for path in files:
        texts = block_texts(path)
        facts = profile.describe("\n".join(texts[: WINDOW[0]]), "\n".join(texts))
        for name in (
            "regulation_type",
            "enacting_body",
            "regulation_number",
            "year",
            "about",
            "effective_date",
        ):
            value = getattr(facts, name)
            if value is not None:
                present[name] += 1
                if name in counts:
                    counts[name][str(value)[:40]] += 1
        expected = expected_number(path.name)
        if expected and facts.regulation_number:
            comparable += 1
            agree += facts.regulation_number.lstrip("0") == expected.lstrip("0")
    print(f"{profile.ref}: files={len(files)}")
    for name in (
        "regulation_type",
        "enacting_body",
        "regulation_number",
        "year",
        "about",
        "effective_date",
    ):
        share = 100 * present[name] / max(len(files), 1)
        print(f"  {name:18s} {present[name]:6d}  ({share:5.1f}%)")
    print(f"number agrees with the file name: {agree}/{comparable}")
    for name, counter in counts.items():
        print(f"\n{name}: {counter.most_common(top)}")


def report_labels(profile: Profile, files: list[Path], *, top: int) -> None:
    entry = get("unit")
    chapters: collections.Counter[str] = collections.Counter()
    articles: collections.Counter[str] = collections.Counter()
    chunks = 0
    for path in files:
        doc = TextExtractor().extract(path, title=path.stem, url=f"file://{path.name}")
        doc = Structurer(profile).apply(doc)
        builder = Builder(
            doc=doc,
            chunker=entry.id,
            version=entry.version,
            config=ChunkConfig(),
            profile=profile,
        )
        for chunk in entry.fn(doc, builder):
            chunks += 1
            chapters[shape(chunk.chapter or "None", 28)] += 1
            articles[shape(chunk.article or "None", 28)] += 1
    print(f"{profile.ref}: files={len(files)} chunks={chunks}")
    print(f"\nchapter shapes: {chapters.most_common(top)}")
    print(f"\narticle shapes: {articles.most_common(top)}")


def report_cleanup(profile: Profile, files: list[Path], *, top: int) -> None:
    rng = random.Random(5)
    seen: dict[int, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    blocks = removed = 0
    for path in files:
        for text in block_texts(path):
            blocks += 1
            stripped = text.strip()
            for index, pattern in enumerate(profile.boilerplate):
                if pattern.match(stripped):
                    seen[index][stripped] += 1
                    removed += 1
                    break
    print(
        f"{profile.ref}: files={len(files)} blocks={blocks} removed={removed}"
        f" ({100 * removed / max(blocks, 1):.2f}%)"
    )
    for index, pattern in enumerate(profile.boilerplate):
        counter = seen[index]
        distinct = list(counter)
        print(
            f"\n[{index}] {pattern.pattern[:72]!r}: {sum(counter.values())} blocks,"
            f" {len(distinct)} distinct"
        )
        print("   most common:", counter.most_common(top))
        print("   random distinct:", rng.sample(distinct, min(top, len(distinct))))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument("report", choices=["structure", "cite", "cleanup", "facts", "labels"])
    parser.add_argument("profile", help="a registry ref, or a path to <id>@<version>.yaml")
    parser.add_argument("directory", type=Path, help="a directory of .md files")
    parser.add_argument(
        "--every", type=int, default=12, help="read every Nth file (default 12)"
    )
    parser.add_argument("--top", type=int, default=10, help="rows per group (default 10)")
    parser.add_argument(
        "--window",
        type=int,
        default=FACTS_BLOCKS,
        help="blocks `facts` treats as the title block",
    )
    args = parser.parse_args()
    WINDOW[0] = args.window

    if not args.directory.is_dir():
        sys.exit(f"{args.directory}: not a directory")
    profile = load_profile(args.profile)
    files = sample_files(args.directory, args.every)
    if not files:
        sys.exit(f"{args.directory}: no non-empty .md files")
    reports = {
        "structure": report_structure,
        "cite": report_cite,
        "cleanup": report_cleanup,
        "facts": report_facts,
        "labels": report_labels,
    }
    reports[args.report](profile, files, top=args.top)


if __name__ == "__main__":
    main()
