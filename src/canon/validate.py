"""Canonical document validation — CANONICAL_FORMAT.md §6.

! Rule 6 (non-empty ratio) is the one that matters most. A failed extraction that
produces valid-but-empty output is worse than a crash: it enters the corpus as a
real document with no content, and nobody notices until retrieval is quietly bad
(LOOPHOLES.md §2).
"""

from __future__ import annotations

from collections import Counter
from enum import StrEnum
from typing import NamedTuple

from canon.models import BlockType, CanonicalDoc
from canon.render import parse_blocks, render_blocks

# Below this share of blocks carrying text, the extraction is presumed failed.
MIN_TEXT_RATIO = 0.30
# Block types that legitimately carry no text of their own.
_TEXTLESS = {BlockType.TABLE_REF, BlockType.FIGURE_REF}


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class Issue(NamedTuple):
    rule: str
    severity: Severity
    message: str
    block_id: str | None = None

    def __str__(self) -> str:
        where = f" [{self.block_id}]" if self.block_id else ""
        return f"{self.severity.value.upper():7} {self.rule}{where}: {self.message}"


def validate(doc: CanonicalDoc, *, min_text_ratio: float = MIN_TEXT_RATIO) -> list[Issue]:
    """Every rule runs; the caller decides what an error means."""
    issues: list[Issue] = []
    issues += _reading_order(doc)
    issues += _table_refs(doc)
    issues += _heading_path(doc)
    issues += _round_trip(doc)
    issues += _text_ratio(doc, min_text_ratio)
    issues += _ids_unique(doc)
    return issues


def is_valid(doc: CanonicalDoc, **kw: object) -> bool:
    return not any(i.severity is Severity.ERROR for i in validate(doc, **kw))  # type: ignore[arg-type]


# -- rule 2 ------------------------------------------------------------------
def _reading_order(doc: CanonicalDoc) -> list[Issue]:
    orders = sorted(b.reading_order for b in doc.blocks)
    if orders != list(range(len(orders))):
        dupes = [o for o, n in Counter(orders).items() if n > 1]
        detail = f"duplicates {dupes}" if dupes else f"gaps in 0..{len(orders) - 1}"
        return [
            Issue(
                "reading_order",
                Severity.ERROR,
                f"reading_order must be a contiguous permutation of 0..n-1 · {detail}",
            )
        ]
    return []


# -- rule 3 ------------------------------------------------------------------
def _table_refs(doc: CanonicalDoc) -> list[Issue]:
    issues: list[Issue] = []
    referenced = Counter(b.ref for b in doc.blocks if b.type is BlockType.TABLE_REF)
    table_ids = {t.id for t in doc.tables}

    for ref, count in referenced.items():
        if ref not in table_ids:
            issues.append(
                Issue("table_refs", Severity.ERROR, f"ref {ref!r} resolves to no table")
            )
        elif count > 1:
            issues.append(
                Issue("table_refs", Severity.ERROR, f"table {ref!r} referenced {count} times")
            )

    for orphan in table_ids - set(referenced):
        issues.append(
            Issue(
                "table_refs",
                Severity.ERROR,
                f"table {orphan!r} is never referenced — it has no position in reading order",
            )
        )

    asset_ids = {a.id for a in doc.assets}
    for block in doc.blocks:
        if block.type is BlockType.FIGURE_REF and block.ref not in asset_ids:
            issues.append(
                Issue(
                    "table_refs",
                    Severity.WARNING,
                    f"figure ref {block.ref!r} has no asset",
                    block.id,
                )
            )
    return issues


# -- rule 4 ------------------------------------------------------------------
def _heading_path(doc: CanonicalDoc) -> list[Issue]:
    """heading_path must track the heading levels that precede a block.

    A heading displaces every open heading at its own level or deeper, and its own
    path ends with its own text. Blocks between headings inherit the current path.

    ! Ancestry is decided by level, not by depth. A profile assigns fixed levels with
    gaps on purpose — `id_regulation` numbers bab 3 and pasal 6 — so that two
    documents chunked at different depths still produce comparable paths. A rule that
    indexed the stack by depth read a level-3 heading following another level-3
    heading as its *child*, and this function reproduced the bug faithfully enough to
    certify it: 931 documents validated clean carrying `Menimbang › Mengingat › BAB I`.

    A validator that re-implements the code it checks does not check it. This one now
    states the invariant — a path is strictly increasing in level — instead of
    replaying how the path was built.
    """
    issues: list[Issue] = []
    stack: list[tuple[int, str]] = []

    for block in doc.ordered():
        if block.type is BlockType.HEADING:
            level = block.level or 1
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, block.text))
        expected = [t for _, t in stack]
        if block.heading_path != expected:
            issues.append(
                Issue(
                    "heading_path",
                    Severity.ERROR,
                    f"heading_path {block.heading_path} does not match structure {expected}",
                    block.id,
                )
            )
    return issues


# -- rule 5 ------------------------------------------------------------------
def _round_trip(doc: CanonicalDoc) -> list[Issue]:
    ordered = doc.ordered()
    reparsed = parse_blocks(render_blocks(ordered))

    if len(reparsed) != len(ordered):
        return [
            Issue(
                "round_trip",
                Severity.ERROR,
                f"render/parse changed block count: {len(ordered)} -> {len(reparsed)}",
            )
        ]

    issues: list[Issue] = []
    for block, (btype, text, level, ref) in zip(ordered, reparsed, strict=True):
        if btype is not block.type:
            issues.append(
                Issue(
                    "round_trip",
                    Severity.ERROR,
                    f"type drift {block.type.value} -> {btype.value}",
                    block.id,
                )
            )
        elif text != block.text.strip():
            issues.append(
                Issue("round_trip", Severity.ERROR, "text drift through render/parse", block.id)
            )
        elif level != block.level or ref != block.ref:
            issues.append(
                Issue(
                    "round_trip",
                    Severity.ERROR,
                    "level/ref drift through render/parse",
                    block.id,
                )
            )
    return issues


# -- rule 6 ------------------------------------------------------------------
def _text_ratio(doc: CanonicalDoc, floor: float) -> list[Issue]:
    candidates = [b for b in doc.blocks if b.type not in _TEXTLESS]
    if not candidates:
        return [
            Issue(
                "text_ratio",
                Severity.ERROR,
                "document has no text-bearing blocks — extraction produced nothing",
            )
        ]
    ratio = sum(1 for b in candidates if b.text.strip()) / len(candidates)
    if ratio < floor:
        return [
            Issue(
                "text_ratio",
                Severity.ERROR,
                f"only {ratio:.0%} of blocks carry text (floor {floor:.0%}) — "
                "extraction is presumed to have failed silently",
            )
        ]
    return []


# -- structural --------------------------------------------------------------
def _ids_unique(doc: CanonicalDoc) -> list[Issue]:
    issues: list[Issue] = []
    for label, ids in (
        ("block", [b.id for b in doc.blocks]),
        ("table", [t.id for t in doc.tables]),
        ("asset", [a.id for a in doc.assets]),
    ):
        for dupe, n in Counter(ids).items():
            if n > 1:
                issues.append(
                    Issue(
                        "ids_unique", Severity.ERROR, f"{label} id {dupe!r} appears {n} times"
                    )
                )
    return issues
