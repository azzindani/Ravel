"""Signals read out of finished chunks, as the profile declares them (`profile.signals`).

References a chunk cites, the terms it defines, the text of tagged blocks (a part's
authority, a section's amendment history, an effective-date note) gathered over the scope
they apply to, and the paragraph designations a chunk runs from and to. `SOURCE_US_CFR.md` §5.

! Disposable, and kept apart from `chunks` (`ENRICHMENT.md` §2). The output is a sidecar
keyed by `chunk_id`; a pattern corrected in the profile re-runs this CPU pass and touches no
chunk and no vector. The profile's `signals` section is excluded from the chunk cache key
for the same reason.

! Nothing here knows a CFR word. The patterns, the formats, the block kinds and the outline's
numbering are the profile's; this file only applies them.
"""

from __future__ import annotations

import functools
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from canon import Block, CanonicalDoc
from chunk.models import Chunk
from spec.models import OutlineSignal, SignalPattern, SignalsSpec

VERSION = "1.0"

_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_DATE = re.compile(r"(?P<m>[A-Z][a-z]{2})[a-z]*\.?\s+(?P<d>\d{1,2}),\s+(?P<y>\d{4})")


@functools.lru_cache(maxsize=512)
def _compiled(pattern: str, ignorecase: bool = False) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE if ignorecase else 0)


def _distinct(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(v for v in values if v))


def _formatted(rule: SignalPattern, text: str, context: dict[str, str]) -> Iterator[str]:
    for match in _compiled(rule.pattern, rule.ignorecase).finditer(text):
        values = {k: re.sub(r"\s+", " ", v).strip() for k, v in match.groupdict().items() if v}
        try:
            yield rule.format.format(**values, **context).strip()
        except KeyError:
            continue  # a group or a context value the match does not have: no value, no guess


def iso(text: str) -> str | None:
    """`Aug. 31, 1989` -> `1989-08-31`."""
    match = _DATE.search(text)
    if not match or match.group("m").lower() not in _MONTHS:
        return None
    try:
        day = date(
            int(match.group("y")), _MONTHS.index(match.group("m").lower()) + 1, int(match["d"])
        )
    except ValueError:
        return None
    return day.isoformat()


# ---------------------------------------------------------------------------
# Outline: `(a)(1)(i)(A)`
# ---------------------------------------------------------------------------
_ROMAN = re.compile(r"^[ivxlcdm]+$")
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


def _roman(text: str) -> int:
    total = 0
    for here, after in zip(text, [*text[1:], ""], strict=True):
        value = _ROMAN_VALUES[here]
        total += -value if after and _ROMAN_VALUES[after] > value else value
    return total


def _letters(text: str) -> int:
    """`a` -> 1, `z` -> 26, `aa` -> 27: the CFR doubles a letter past `z`."""
    return (len(text) - 1) * 26 + (ord(text[0].lower()) - 96)


def _value(marker: str, kind: str) -> int | None:
    if kind == "digit":
        return int(marker) if marker.isdigit() else None
    if kind == "roman":
        return _roman(marker) if _ROMAN.match(marker) else None
    if kind == "lower":
        return (
            _letters(marker)
            if marker.isalpha() and marker.islower() and len(set(marker)) == 1
            else None
        )
    if kind == "upper":
        return (
            _letters(marker)
            if marker.isalpha() and marker.isupper() and len(set(marker)) == 1
            else None
        )
    return None


@dataclass
class Outline:
    """The designation in force while a unit's paragraphs are read in order."""

    spec: OutlineSignal
    stack: list[tuple[int, str]] = field(default_factory=list)

    def read(self, text: str, after: str | None = None) -> str | None:
        """The full designation of a paragraph, or None when it opens with no marker.

        `after` is the next paragraph's text: where a marker reads two ways, the marker that
        follows it says which.
        """
        markers = self._markers(text)
        if not markers:
            return None
        if self.spec.inline and (inline := _compiled(self.spec.inline).match(text)):
            markers += re.findall(r"\(\s*([^()\s]+)\s*\)", inline.group(1))
        following = self._markers(after)[:1] if after else []
        for position, marker in enumerate(markers):
            last = position == len(markers) - 1
            self._place(
                marker,
                deeper_only=position > 0,
                after=following[0] if last and following else None,
            )
        return "".join(f"({m})" for _, m in self.stack)

    def _markers(self, text: str) -> list[str]:
        match = _compiled(self.spec.marker).match(text)
        return re.findall(r"\(\s*([^()\s]+)\s*\)", match.group(1)) if match else []

    def _place(self, marker: str, *, deeper_only: bool, after: str | None = None) -> None:
        levels = self.spec.levels
        fits = [i for i, kind in enumerate(levels) if _value(marker, kind) is not None]
        if not fits:
            return
        # The level below the innermost in force: a section may open at `(1)`, level 1.
        depth = self.stack[-1][0] + 1 if self.stack else 0
        # The first of a new, deeper level: `(1)` under `(a)`, `(i)` under `(1)`.
        deeper = depth if depth in fits and _value(marker, levels[depth]) == 1 else None
        choice: int | None
        if deeper_only:
            later = next((i for i in fits if i >= depth), None)
            choice = deeper if deeper is not None else later
        else:
            # The innermost sequence in force that this marker continues: `(v)` after `(iv)`.
            continued = next(
                (
                    level
                    for level, current in reversed(self.stack)
                    if level in fits and _follows(marker, current, levels[level])
                ),
                None,
            )
            innermost = self.stack[-1][0] if self.stack else None
            if continued is not None and continued == innermost:
                choice = continued
            elif deeper is not None and continued is not None:
                # ! `(i)` under `(h)(1)` opens the numerals or continues the letters. The
                # next marker decides: `(ii)` follows the numeral, `(j)` the letter. With
                # neither, the deeper reading, as the CFR's drafting rules order it.
                choice = (
                    continued
                    if after is not None and _follows(after, marker, levels[continued])
                    else deeper
                )
            elif deeper is not None:
                choice = deeper
            else:
                choice = continued
        if choice is None:
            choice = min((i for i in fits if i <= depth), default=fits[0])
        self.stack = [entry for entry in self.stack if entry[0] < choice]
        self.stack.append((choice, marker))


def _follows(marker: str, previous: str, kind: str) -> bool:
    value, before = _value(marker, kind), _value(previous, kind)
    return value is not None and before is not None and value == before + 1


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------
@dataclass
class SignalsReport:
    chunks: int = 0
    present: dict[str, int] = field(default_factory=dict)


def columns_of(spec: SignalsSpec) -> list[str]:
    """Every column the spec produces, in a fixed order."""
    names = [*spec.lists]
    for name, block in spec.blocks.items():
        names.append(name)
        if block.pattern and "date" in _compiled(block.pattern.pattern).groupindex:
            names.append(f"{name}_latest")
    if spec.definitions:
        names.append("defined_terms")
    if spec.outline:
        names += ["paragraph_first", "paragraph_last"]
    return names


def compute(
    chunks: Iterable[Chunk], docs: Iterable[CanonicalDoc], spec: SignalsSpec
) -> Iterator[dict[str, Any]]:
    """One row per chunk: `chunk_id` and every column of `columns_of(spec)`.

    Chunks arrive in the order chunking wrote them, which is the order of the documents;
    the two streams are read in step, so a corpus never has to be held in memory.
    """
    rows: list[dict[str, Any]] = []
    scoped: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    #: `nearest`: scope -> key -> heading path printed above the blocks -> their values.
    placed: dict[str, dict[str, dict[tuple[str, ...], list[str]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    paths: list[tuple[str, ...]] = []
    strip = _compiled(spec.heading_strip, True) if spec.heading_strip else None

    def path_of(block: Block) -> tuple[str, ...]:
        return tuple(strip.sub("", h).strip() if strip else h for h in block.heading_path)

    outlines: dict[str, Outline] = {}
    documents = iter(docs)
    doc: CanonicalDoc | None = None
    blocks: dict[str, Block] = {}

    for chunk in chunks:
        while doc is None or doc.doc_id != chunk.doc_id:
            doc = next(documents, None)
            if doc is None:
                raise ValueError(f"chunk {chunk.id}: no canonical document {chunk.doc_id}")
            blocks = {b.id: b for b in doc.blocks}
            outlines.clear()
        own = [blocks[i] for i in chunk.block_ids if i in blocks]
        context = _context(chunk, spec)
        row: dict[str, Any] = {"chunk_id": chunk.id}

        for name, rules in spec.lists.items():
            found = (v for rule in rules for v in _formatted(rule, chunk.body, context))
            row[name] = [v for v in _distinct(found) if v != chunk.identifier]

        for name, rule in spec.blocks.items():
            matched = [
                b
                for b in own
                if (rule.kind is None or b.attrs.get("kind") == rule.kind)
                and (rule.tag is None or b.attrs.get("tag") == rule.tag)
            ]
            texts = [b.text for b in matched]
            values = (
                [v for t in texts for v in _formatted(rule.pattern, t, context)]
                if rule.pattern
                else texts
            )
            if rule.scope == "chunk":
                row[name] = values
            else:
                key = str(getattr(chunk, rule.scope, "") or "")
                if key and rule.nearest:
                    for block in matched:
                        read = (
                            list(_formatted(rule.pattern, block.text, context))
                            if rule.pattern
                            else [block.text]
                        )
                        placed[name][key][path_of(block)].extend(read)
                elif key:
                    scoped[name][key].extend(values)
                row[name] = key  # resolved once every chunk of the scope has been read

        if spec.definitions:
            row["defined_terms"] = _definitions(own, spec)

        if spec.outline:
            key = str(getattr(chunk, spec.outline.scope, "") or chunk.id)
            outline = outlines.setdefault(key, Outline(spec.outline))
            texts = [b.text for b in own]
            designations = [
                d
                for i, text in enumerate(texts)
                if (d := outline.read(text, texts[i + 1] if i + 1 < len(texts) else None))
            ]
            row["paragraph_first"] = designations[0] if designations else None
            row["paragraph_last"] = designations[-1] if designations else None
        rows.append(row)
        paths.append(path_of(own[0]) if own else ())

    for row, path in zip(rows, paths, strict=True):
        for name, rule in spec.blocks.items():
            if rule.scope == "chunk":
                values = row[name]
            elif rule.nearest:
                values = _nearest(placed[name].get(row[name], {}), path) if row[name] else []
            else:
                values = _distinct(scoped[name].get(row[name], [])) if row[name] else []
            dated = (
                rule.pattern is not None
                and "date" in _compiled(rule.pattern.pattern).groupindex
            )
            if rule.pattern:
                row[name] = _distinct(values)
                if dated:
                    days = [d for v in row[name] if (d := iso(v))]
                    row[f"{name}_latest"] = max(days) if days else None
            else:
                row[name] = " ".join(_distinct(values)) or None
        yield row


def _nearest(placed: dict[tuple[str, ...], list[str]], path: tuple[str, ...]) -> list[str]:
    """The values printed in the deepest unit enclosing `path`; with none, the shallowest
    printed, which is the scope's own (a part's source, read in a chunk the part's later
    volume holds under another chapter heading)."""
    if not placed:
        return []
    enclosing = [p for p in placed if path[: len(p)] == p]
    if enclosing:
        return placed[max(enclosing, key=len)]
    shallowest = min(len(p) for p in placed)
    return [v for p, values in placed.items() if len(p) == shallowest for v in values]


def _context(chunk: Chunk, spec: SignalsSpec) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, pattern in spec.context.items():
        value = getattr(chunk, name, None)
        if value and (match := _compiled(pattern).search(str(value))):
            out.update({f"ctx_{k}": v for k, v in match.groupdict().items() if v})
    return out


def _definitions(blocks: list[Block], spec: SignalsSpec) -> list[str]:
    rule = spec.definitions
    assert rule is not None
    terms: list[str] = []
    for block in blocks:
        match = _compiled(rule.pattern).match(block.text)
        if not match or not match.groupdict().get("term"):
            continue
        term = match.group("term").strip(" ,.;:\"'“”")
        if rule.emphasised:
            emphasis = {e.strip(" ,.;:\"'“”") for e in block.attrs.get("emphasis") or []}
            if term not in emphasis:
                continue
        terms.append(term)
    return _distinct(terms)


def schema_of(spec: SignalsSpec) -> pa.Schema:
    fields = [pa.field("chunk_id", pa.string(), nullable=False)]
    for name in columns_of(spec):
        scalar = (
            name.endswith("_latest")
            or name.startswith("paragraph_")
            or (name in spec.blocks and spec.blocks[name].pattern is None)
        )
        fields.append(pa.field(name, pa.string() if scalar else pa.list_(pa.string())))
    return pa.schema(fields)


def write(
    rows: Iterable[dict[str, Any]],
    spec: SignalsSpec,
    out_dir: Path,
    *,
    shard_rows: int = 100_000,
) -> SignalsReport:
    """Write the sidecar as parquet shards and count what each column found."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("part-*.parquet"):
        old.unlink()
    schema = schema_of(spec)
    report = SignalsReport(present={name: 0 for name in columns_of(spec)})
    buffer: list[dict[str, Any]] = []
    shard = 0

    def flush() -> None:
        nonlocal shard
        if buffer:
            table = pa.Table.from_pylist(buffer, schema=schema)
            pq.write_table(table, out_dir / f"part-{shard:05d}.parquet", compression="zstd")
            shard += 1
            buffer.clear()

    for row in rows:
        report.chunks += 1
        for name in report.present:
            report.present[name] += bool(row.get(name))
        buffer.append(row)
        if len(buffer) >= shard_rows:
            flush()
    flush()
    return report
