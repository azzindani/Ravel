"""Verify a knowledge-base build against the documents it came from · read-only, measured.

    python tools/verify_kb.py id_regulation_md@1.2 work/id_reg_markdown --every 23 --workers 8

Runs the real pipeline in memory (`TextExtractor` -> `Structurer` -> the profile's chunker and
config) and answers one question per document: **did anything the document says fail to
reach a chunk, and if so, where did it go?**

Loss accounting
    Every block of the canonical document lands in exactly one bucket: `emitted` (in a
    chunk's `block_ids`), `heading_carried` (a heading whose text is in a chunk's path or
    labels), `consumed_title` (a chapter title used as a label), `boilerplate` (dropped by a
    profile rule, counted per rule), `non_body` (a block type chunks never carry),
    `empty`, and **`unaccounted`** -- text no rule claims. Unaccounted must be zero; a
    `heading_lost` heading, one whose text appears in no chunk, is reported beside it.

Verbatim check
    Every chunk's `body` must equal its own blocks' text joined, character for character.
    A chunk that says anything else is a chunk that cites text the source does not contain.

Word recall
    The multiset of words in the source file against the canonical blocks and against the
    chunk bodies, and the words that went missing at each step. This is the check that does
    not trust the block accounting: it counts what a reader could search for.

Also: throughput per stage, peak memory, chunk size distribution, the profile's own
`expected` floors, fact and label coverage, and the worst documents by recall.

! Nothing here writes except `--out`. It reads files under the directory you name.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from canon import Block, BlockType  # noqa: E402
from chunk import Builder, Chunk, check, get  # noqa: E402
from chunk.base import BODY_TYPES  # noqa: E402
from chunk.unit import _split_marker, _where, truncated_duplicates  # noqa: E402
from extract.structure import Structurer  # noqa: E402
from extract.text import TextExtractor  # noqa: E402
from spec import Profile, default_registry  # noqa: E402
from spec.corpus import ChunkSpec  # noqa: E402

WORD = re.compile(r"\w+")
ENTITIES = frozenset({"lt", "gt", "amp", "quot", "nbsp", "apos"})
SQUASH = re.compile(r"\W+")

#: Facts and labels whose coverage is reported per chunk.
FIELDS = (
    "regulation_type",
    "enacting_body",
    "regulation_number",
    "year",
    "about",
    "effective_date",
    "identifier",
    "article",
    "chapter",
)


def words(text: str) -> Counter[str]:
    return Counter(WORD.findall(text.lower()))


def squash(text: str) -> str:
    return SQUASH.sub("", text).lower()


def peak_memory_mb() -> float:
    """Peak resident memory of this process, or 0.0 where the platform will not say."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        kernel, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(Counters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo(
            kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb
        )
        return float(counters.PeakWorkingSetSize) / (1024 * 1024)
    import resource

    peak = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return peak / (1024 * 1024 if sys.platform == "darwin" else 1024)


def load(ref: str) -> Profile:
    return default_registry().get(ref)


# ---------------------------------------------------------------------------
# one document
# ---------------------------------------------------------------------------
def account(
    blocks: list[Block],
    chunks: list[Chunk],
    consumed: set[str],
    profile: Profile,
    titled: set[str],
    repeated: set[str],
) -> dict[str, Any]:
    """Put every block in exactly one bucket. Returns counts and characters per bucket."""
    emitted = {bid for c in chunks for bid in c.block_ids}
    # Whole labels, not one joined string: concatenated, neighbouring labels can contain
    # a heading that no single chunk carries.
    hay = {
        squash(part)
        for c in chunks
        for part in (c.heading_path, c.chapter, c.article, c.locator_section)
        if part
    }
    count: Counter[str] = Counter()
    chars: Counter[str] = Counter()
    rules: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}

    def put(bucket: str, block: Block, example: bool = False) -> None:
        size = len(block.text.strip())
        count[bucket] += 1
        chars[bucket] += size
        if example and len(examples.setdefault(bucket, [])) < 3:
            examples[bucket].append(block.text.strip()[:90])

    for block in blocks:
        text = block.text.strip()
        if not text:
            put("empty", block)
        elif block.id in emitted:
            put("emitted", block)
        elif block.id in consumed:
            put("consumed_title", block)
        elif block.id in repeated:
            # Cut off at a page break and printed again in full by the next block.
            put("truncated_duplicate", block, example=True)
        elif block.type is BlockType.HEADING:
            if squash(text) in hay or any(squash(text) in part for part in hay):
                put("heading_carried", block)
            elif block.id in titled:
                put(f"heading_lost:{block.attrs.get('unit') or 'none'}", block, example=True)
            else:
                # A bare marker with nothing under it (`Pasal 10`, then a removed
                # `Cukup jelas.`) says nothing a chunk is missing.
                put("empty_section", block)
        elif block.type not in BODY_TYPES:
            put(f"non_body:{block.type.value}", block, example=True)
        else:
            rule = next((p.pattern for p in profile.boilerplate if p.match(text)), None)
            if rule is not None:
                put("boilerplate", block)
                rules[rule[:70]] += 1
            else:
                put("unaccounted", block, example=True)
    return {
        "count": dict(count),
        "chars": dict(chars),
        "rules": dict(rules),
        "examples": examples,
    }


def verbatim_failures(blocks: list[Block], chunks: list[Chunk]) -> list[str]:
    """Chunks whose body is not exactly their own blocks' text, joined."""
    by_id = {b.id: b for b in blocks}
    bad: list[str] = []
    for c in chunks:
        if c.attrs.get("kind") == "table":
            continue
        texts = [by_id[i].text.strip() for i in c.block_ids if i in by_id]
        expected = "\n\n".join(t for t in texts if t)
        if c.body != expected:
            bad.append(f"{c.locator_section or c.heading_path or c.id}")
    return bad


def run_one(args: tuple[str, str, str]) -> dict[str, Any]:
    """Everything measured about one file. Pure function of its arguments, so it can run
    in a worker."""
    ref, path_text, name = args
    path = Path(path_text)
    profile = load(ref)
    config = ChunkSpec().to_config(profile)
    entry = get(profile.spec.pipeline.chunk.chunker or "unit")
    result: dict[str, Any] = {"name": name, "bytes": path.stat().st_size}
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        started = time.perf_counter()
        doc = TextExtractor().extract(path, title=path.stem, url=f"file://{name}")
        extracted = time.perf_counter()
        doc = Structurer(profile).apply(doc)
        structured = time.perf_counter()
        builder = Builder(
            doc=doc,
            chunker=entry.id,
            version=entry.version,
            config=config,
            profile=profile,
        )
        chunks = list(entry.fn(doc, builder))
        done = time.perf_counter()
    except Exception as exc:  # noqa: BLE001 - one document must not kill a verification
        result["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return result

    blocks = sorted(doc.blocks, key=lambda b: b.reading_order)
    _, title_of = _where(doc, profile)
    consumed = {b.id for b in title_of.values()}
    repeated = truncated_duplicates(doc, profile)
    units = {u.name: u for u in profile.units}
    titled = {
        b.id
        for b in blocks
        if b.type is BlockType.HEADING
        and (
            b.id in title_of
            or not (u := units.get(str(b.attrs.get("unit"))))
            or _split_marker(u, b.text)[1]
        )
    }
    src_words = words(source)
    canon_words = words("\n".join(b.text for b in blocks))
    body_words = words("\n".join(c.body for c in chunks))
    carried_words = body_words + words(
        "\n".join(
            part
            for c in chunks
            for part in (c.heading_path, c.chapter, c.article, c.locator_section)
            if part
        )
    )
    total = sum(src_words.values()) or 1
    missing_canon = src_words - canon_words
    # A dropped cut copy is not lost: the block after it carries every one of its words.
    repeat_words = words(" ".join(b.text for b in blocks if b.id in repeated))
    missing_chunks = canon_words - carried_words - repeat_words
    lost_kinds: Counter[str] = Counter()
    other_missing: Counter[str] = Counter()
    for token, n in missing_canon.items():
        if token in ENTITIES:
            lost_kinds["html_entity (decoded, not lost)"] += n
        elif token.isdigit() and len(token) <= 3:
            lost_kinds["list_number"] += n
        else:
            lost_kinds["other"] += n
            other_missing[token] = n

    result.update(
        {
            "seconds": {
                "extract": extracted - started,
                "structure": structured - extracted,
                "chunk": done - structured,
            },
            "blocks": len(blocks),
            "chunks": len(chunks),
            "tokens": [c.token_count for c in chunks],
            "parts": sum(c.part[1] > 1 for c in chunks),
            "present": {
                f: sum(getattr(c, f, None) not in (None, "") for c in chunks) for f in FIELDS
            },
            "words": {
                "source": total,
                "canonical": sum(canon_words.values()),
                "chunks": sum(body_words.values()),
                "carried": sum(carried_words.values()),
                "lost_in_extract": sum(missing_canon.values()),
                "lost_in_chunk": sum(missing_chunks.values()),
            },
            "recall_body": sum(body_words.values()) / total,
            "recall_carried": min(1.0, sum((src_words & carried_words).values()) / total),
            "lost_kinds": dict(lost_kinds),
            "missing_extract": dict(other_missing.most_common(25)),
            "missing_chunk": dict(missing_chunks.most_common(25)),
            "accounting": account(blocks, chunks, consumed, profile, titled, repeated),
            "verbatim_bad": verbatim_failures(blocks, chunks),
            "duplicated_blocks": sum(
                n > 1 for n in Counter(i for c in chunks for i in c.block_ids).values()
            ),
        }
    )
    if not chunks:
        buckets = result["accounting"]["chars"]
        result["empty_reason"] = max(buckets, key=lambda k: buckets[k], default="no blocks")
        result["empty_content_chars"] = sum(
            n for k, n in buckets.items() if k not in ("boilerplate", "empty", "empty_section")
        )
    result["peak_mb"] = peak_memory_mb()
    return result


# ---------------------------------------------------------------------------
# the corpus
# ---------------------------------------------------------------------------
def pct(sorted_values: list[int], fraction: float) -> int:
    return sorted_values[min(len(sorted_values) - 1, int(len(sorted_values) * fraction))]


def summarise(
    profile: Profile, results: list[dict[str, Any]], wall: float, workers: int
) -> dict[str, Any]:
    good = [r for r in results if "error" not in r]
    failed = [r for r in results if "error" in r]
    empty = [r for r in good if r["chunks"] == 0]
    chunks = sum(r["chunks"] for r in good)
    tokens = sorted(t for r in good for t in r["tokens"])

    count: Counter[str] = Counter()
    chars: Counter[str] = Counter()
    rules: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}
    for r in good:
        count.update(r["accounting"]["count"])
        chars.update(r["accounting"]["chars"])
        rules.update(r["accounting"]["rules"])
        for bucket, items in r["accounting"]["examples"].items():
            slot = examples.setdefault(bucket, [])
            slot.extend(f"{r['name'][:36]}: {i}" for i in items[: 3 - len(slot)])

    words_total: Counter[str] = Counter()
    lost_kinds: Counter[str] = Counter()
    for r in good:
        words_total.update(r["words"])
        lost_kinds.update(r["lost_kinds"])
    empty_bytes = sorted(r["bytes"] for r in empty)
    empty_why = Counter(r["empty_reason"] for r in empty)
    empty_content = sum(r["empty_content_chars"] for r in empty)
    missing_extract: Counter[str] = Counter()
    missing_chunk: Counter[str] = Counter()
    for r in good:
        missing_extract.update(r["missing_extract"])
        missing_chunk.update(r["missing_chunk"])

    present: Counter[str] = Counter()
    for r in good:
        present.update(r["present"])

    seconds: Counter[str] = Counter()
    for r in good:
        seconds.update(r["seconds"])
    cpu = sum(seconds.values()) or 1.0

    verbatim = [(r["name"], v) for r in good for v in r["verbatim_bad"]]
    worst = sorted(good, key=lambda r: r["recall_body"])[:15]

    expected = profile.spec.pipeline.expected
    cap = expected.max_row_tokens
    oversize = sum(t > cap for t in tokens) if cap else 0
    violations = (
        check(expected, present=present, chunks=chunks, oversize=oversize) if chunks else []
    )

    gates = {
        "no document failed": not failed,
        "no unaccounted block": count["unaccounted"] == 0,
        "no titled heading lost": not any(k.startswith("heading_lost:") for k in count),
        "every chunk body is verbatim": not verbatim,
        "every zero-chunk document held only boilerplate": empty_content == 0,
        "profile's expected floors met": not violations,
    }
    return {
        "profile": profile.ref,
        "files": len(results),
        "failed": [(r["name"], r["error"]) for r in failed][:20],
        "empty": [r["name"] for r in empty][:20],
        "empty_count": len(empty),
        "empty_bytes": {
            "median": pct(empty_bytes, 0.5) if empty_bytes else 0,
            "max": empty_bytes[-1] if empty_bytes else 0,
            "total": sum(empty_bytes),
        },
        "empty_reasons": empty_why.most_common(),
        "empty_content_chars": empty_content,
        "failed_count": len(failed),
        "lost_kinds": dict(lost_kinds),
        "chunks": chunks,
        "chunks_per_file": chunks / max(len(good), 1),
        "tokens": {
            "p10": pct(tokens, 0.1) if tokens else 0,
            "p50": pct(tokens, 0.5) if tokens else 0,
            "p90": pct(tokens, 0.9) if tokens else 0,
            "p99": pct(tokens, 0.99) if tokens else 0,
            "max": tokens[-1] if tokens else 0,
            "under_8": sum(t < 8 for t in tokens),
            "under_50": sum(t < 50 for t in tokens),
            "under_100": sum(t < 100 for t in tokens),
            "oversize": oversize,
            "split": sum(r["parts"] for r in good),
        },
        "accounting": {"count": dict(count), "chars": dict(chars)},
        "boilerplate_rules": rules.most_common(12),
        "examples": examples,
        "words": dict(words_total),
        "missing_after_extract": missing_extract.most_common(20),
        "missing_after_chunk": missing_chunk.most_common(20),
        "coverage": {f: present[f] / chunks if chunks else 0.0 for f in FIELDS},
        "performance": {
            "workers": workers,
            "wall_seconds": wall,
            "cpu_seconds": dict(seconds),
            "files_per_second_wall": len(results) / wall if wall else 0.0,
            "files_per_second_per_core": len(good) / cpu,
            "megabytes_per_second_per_core": sum(r["bytes"] for r in good) / 1e6 / cpu,
            "peak_memory_mb_worker": max((r.get("peak_mb", 0.0) for r in good), default=0.0),
        },
        "verbatim_failures": verbatim[:20],
        "violations": [str(v) for v in violations],
        "worst_recall": [
            (r["name"], round(r["recall_body"], 3), r["chunks"], r["bytes"]) for r in worst
        ],
        "gates": gates,
    }


def report(s: dict[str, Any]) -> None:
    w = s["words"]
    total = w.get("source", 1)
    c = s["accounting"]
    n_blocks = sum(c["count"].values()) or 1
    n_chars = sum(c["chars"].values()) or 1

    print(
        f"\n{s['profile']} · {s['files']:,} files · {s['chunks']:,} chunks "
        f"({s['chunks_per_file']:.1f}/file)"
    )

    print("\nLOSS ACCOUNTING (every block, one bucket)")
    for bucket in sorted(c["count"], key=lambda b: -c["chars"].get(b, 0)):
        print(
            f"  {bucket:26s} {c['count'][bucket]:>10,} blocks "
            f"({100 * c['count'][bucket] / n_blocks:5.2f}%)  "
            f"{c['chars'][bucket]:>12,} chars ({100 * c['chars'][bucket] / n_chars:5.2f}%)"
        )
    if s["boilerplate_rules"]:
        print("  dropped by rule:")
        for rule, k in s["boilerplate_rules"]:
            print(f"    {k:>9,}  {rule!r}")
    for bucket in sorted(s["examples"]):
        if bucket == "unaccounted" or bucket.startswith("heading_lost"):  # titled only
            for ex in s["examples"][bucket][:2]:
                print(f"  e.g. {bucket}: {ex!r}")

    print("\nWORD RECALL (what a reader could search for)")
    print(f"  source words            {w['source']:>12,}")
    print(
        f"  lost at extraction      {w['lost_in_extract']:>12,}  "
        f"({100 * w['lost_in_extract'] / total:5.2f}%)"
    )
    for kind, n in sorted(s["lost_kinds"].items(), key=lambda kv: -kv[1]):
        print(f"      {kind:14s} {n:>12,}  ({100 * n / total:5.2f}%)")
    print(f"      top of 'other': {s['missing_after_extract'][:10]}")
    print(
        f"  lost at chunking        {w['lost_in_chunk']:>12,}  "
        f"({100 * w['lost_in_chunk'] / total:5.2f}%)  top: {s['missing_after_chunk'][:8]}"
    )
    share = 100 * w["chunks"] / total
    print(f"  in chunk bodies         {w['chunks']:>12,}  ({share:5.2f}% of source)")

    t = s["tokens"]
    print("\nCHUNKS")
    spread = "/".join(str(t[q]) for q in ("p10", "p50", "p90", "p99"))
    print(f"  tokens p10/p50/p90/p99/max  {spread}/{t['max']:,}")
    k = max(s["chunks"], 1)
    rates = {q: 100 * t[q] / k for q in ("under_8", "under_50", "under_100", "split")}
    print(
        f"  under 8: {rates['under_8']:.1f}%   under 50: {rates['under_50']:.1f}%   "
        f"under 100: {rates['under_100']:.1f}%   split into parts: {rates['split']:.1f}%   "
        f"oversize: {t['oversize']:,}"
    )
    print("  coverage  " + "  ".join(f"{f} {100 * v:.1f}%" for f, v in s["coverage"].items()))

    p = s["performance"]
    cpu = p["cpu_seconds"]
    print("\nPERFORMANCE")
    print(
        f"  {p['files_per_second_per_core']:.1f} files/s/core · "
        f"{p['megabytes_per_second_per_core']:.2f} MB/s/core · "
        f"wall {p['wall_seconds']:.0f}s on {p['workers']} workers "
        f"({p['files_per_second_wall']:.1f} files/s)"
    )
    print("  cpu seconds by stage: " + ", ".join(f"{k} {v:.0f}" for k, v in cpu.items()))
    print(f"  peak memory (largest worker process): {p['peak_memory_mb_worker']:.0f} MB")

    if s["worst_recall"]:
        print("\nLOWEST BODY RECALL (open these)")
        for name, recall, chunks, size in s["worst_recall"][:8]:
            print(f"  {recall:6.1%}  {chunks:>4} chunks  {size / 1000:>7.1f} kB  {name}")
    for name, error in s["failed"][:5]:
        print(f"  FAILED {name}: {error}")
    if s["empty_count"]:
        eb = s["empty_bytes"]
        print(
            f"  ZERO CHUNKS: {s['empty_count']:,} documents, median {eb['median']:,} bytes, "
            f"largest {eb['max']:,}, {eb['total']:,} bytes in all; "
            f"largest bucket per document: {s['empty_reasons'][:5]}; "
            f"characters outside boilerplate: {s['empty_content_chars']:,}"
        )
    for name in s["empty"][:5]:
        print(f"    {name}")
    for name, where in s["verbatim_failures"][:5]:
        print(f"  NOT VERBATIM {name}: {where}")
    for v in s["violations"]:
        print(f"  BELOW EXPECTED {v}")

    print("\nGATES")
    for name, ok in s["gates"].items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument("profile", help="a registry ref, e.g. id_regulation_md@1.2")
    parser.add_argument("directory", type=Path, help="a directory of .md files")
    parser.add_argument(
        "--every", type=int, default=23, help="read every Nth file (default 23)"
    )
    parser.add_argument("--limit", type=int, default=0, help="stop after N files")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--out", type=Path, default=None, help="write the full summary as JSON")
    args = parser.parse_args()

    if not args.directory.is_dir():
        sys.exit(f"{args.directory}: not a directory")
    profile = load(args.profile)
    files = sorted(p for p in args.directory.rglob("*.md") if p.stat().st_size > 0)
    files = files[:: max(1, args.every)]
    if args.limit:
        files = files[: args.limit]
    jobs = [(args.profile, str(p), p.name) for p in files]

    started = time.perf_counter()
    if args.workers > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            results = list(pool.map(run_one, jobs, chunksize=8))
    else:
        results = [run_one(job) for job in jobs]
    wall = time.perf_counter() - started

    summary = summarise(profile, results, wall, args.workers)
    report(summary)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    sys.exit(0 if all(summary["gates"].values()) else 1)


if __name__ == "__main__":
    main()
