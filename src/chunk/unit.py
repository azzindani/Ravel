"""Split on structural units, then on sub-units within them.

The reference chunker, and the one `CHUNKING.md` §3 calls `id_regulation`. It is
named `unit` instead, deliberately: nothing in this file knows what a *pasal* is. It
splits on whichever units the document's profile declares, reads sub-unit markers from
the profile's patterns, and builds citations from the profile's abbreviations. Pointed
at `id_regulation@1.0` it produces `Perbup 15/2011 Pasal 9 ayat (3)`; pointed at a
contract profile it would produce clause citations from the same code.

That is principle 12 (`CLAUDE.md` §5) applied to the stage where breaking it did the
most damage. The incumbent corpus was built by a parser that hardcoded
`PERATURAN MENTERI KEUANGAN`, and 10.66% of its 748,558 rows carry
`regulation_type = Unknown` as a result (`ABSORPTION.md` §4). A chunker with an
Indonesian word in it is that defect starting over.

What it does, in order:

1. Read the document's own citation once, from its opening blocks.
2. Walk blocks in reading order, starting a segment at every heading whose unit is in
   `split_on` (default: the deepest units the profile declares).
3. Inside a segment, start a new group at every sub-unit marker, carrying the unit's
   lead-in text into the first one so an `ayat` is not stranded without its subject.
4. Emit, splitting anything over `max_tokens` into `part n of m`, and merging anything
   under `min_tokens` forward.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

from canon import Block, BlockType, CanonicalDoc
from chunk.base import BODY_TYPES, Builder, ChunkConfig, chunker
from chunk.models import Chunk
from spec import Profile

VERSION = "1.0"

#: Blocks scanned for the document's own citation. The identity of a regulation is in
#: its title block; reading further invites matching a cross-reference to a *different*
#: regulation and stamping the whole document with someone else's number.
CITATION_BLOCKS = 12


@dataclass(slots=True)
class Segment:
    """One structural unit: its heading, and everything under it."""

    heading: Block | None
    blocks: list[Block] = field(default_factory=list)

    @property
    def label(self) -> str:
        # Trailing punctuation is layout, not part of the name a citation uses:
        # `Mengingat:` is cited as `Mengingat`.
        return self.heading.text.strip().rstrip(":;.") if self.heading else ""

    @property
    def level(self) -> int:
        return (self.heading.level or 1) if self.heading else 0


@dataclass(slots=True)
class Group:
    """A sub-unit inside a segment, or a segment's lead-in when `marker` is empty."""

    marker: str
    blocks: list[Block] = field(default_factory=list)


@dataclass(slots=True)
class Plan:
    """A chunk decided but not yet built.

    The chunker works in plans so that size adjustments happen over *blocks*, before
    any chunk has asserted where it came from.
    """

    blocks: list[Block]
    locator: str
    identifier: str | None
    attrs: dict[str, str]
    lead: str = ""
    lead_blocks: list[str] = field(default_factory=list)
    """Ids of the blocks the lead-in came from, so it stays traceable."""

    def preview(self, builder: Builder) -> str:
        """The body this plan would produce, for size decisions only."""
        body = builder.body_of(self.blocks)
        return f"{self.lead}\n\n{body}" if self.lead else body


@chunker("unit", version=VERSION)
def chunk_by_unit(doc: CanonicalDoc, builder: Builder) -> Iterator[Chunk]:
    """Structural units from the document's profile, then sub-units within them."""
    profile = builder.profile
    config = builder.config
    split_on = _split_on(profile, config.params.get("split_on"))
    use_subunits = bool(config.params.get("subunits", True)) and profile is not None
    citation = _citation(doc, profile)

    plans: list[Plan] = []
    for segment in _segments(doc, split_on):
        here: list[Plan] = []
        body = [b for b in segment.blocks if _is_body(b, profile)]
        if not body:
            continue

        groups = _groups(body, profile) if use_subunits else [Group("", body)]
        lead = _lead(groups, config, builder)
        lead_ids = [b.id for b in groups[0].blocks] if lead else []

        for group in groups:
            if not group.blocks:
                continue
            if lead and not group.marker:
                continue  # the lead is carried into the sub-units, not emitted twice
            locator = " ".join(p for p in (segment.label, group.marker) if p)
            here.append(
                Plan(
                    blocks=group.blocks,
                    locator=locator,
                    identifier=_identifier(citation, locator),
                    attrs=_attrs(segment, group),
                    lead=lead if group.marker else "",
                    lead_blocks=lead_ids if group.marker else [],
                )
            )

        # ! Coalesced per segment, never across them. Merging a short `Menimbang` into
        # the pasal that follows produced one chunk labelled `Menimbang` whose body was
        # the pasal's — a locator pointing at the wrong section, which is the one defect
        # in a chunk that no downstream stage can detect.
        plans.extend(_coalesce(here, builder))

    for plan in plans:
        yield from builder.split(
            plan.blocks,
            locator=plan.locator or None,
            identifier=plan.identifier,
            attrs=plan.attrs,
            lead=plan.lead,
            lead_block_ids=plan.lead_blocks,
        )
    yield from builder.tables()


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------
def _split_on(profile: Profile | None, configured: object) -> frozenset[str]:
    """Which unit names begin a new chunk.

    Defaults to the deepest units the profile declares — the finest structure it can
    see, which is the level a reader cites. Sections such as `menimbang` are excluded
    from the default: they are containers, not citable units.
    """
    if isinstance(configured, list | tuple | set):
        return frozenset(str(name) for name in configured)
    if profile is None:
        return frozenset()
    units = profile.spec.structure.units
    if not units:
        return frozenset()
    deepest = max(u.level for u in units)
    return frozenset(u.name for u in units if u.level == deepest)


def _segments(doc: CanonicalDoc, split_on: frozenset[str]) -> Iterator[Segment]:
    """Blocks grouped by splitting unit, in reading order.

    Content before the first splitting unit is its own segment under whatever heading
    precedes it. A regulation's `Menimbang` and `Mengingat` are real content — 82,313
    rows of the incumbent corpus — and dropping them because no pasal has started yet
    would lose the part of a document that states why it exists.

    ! A heading also closes the current segment when it is a *sibling or ancestor* of
    the one that opened it, not only when it is a splitting unit. Without that,
    `Mengingat` was absorbed as a block inside the `Menimbang` segment, and its chunks
    went out labelled `Menimbang` while their heading_path correctly said `Mengingat` —
    a locator pointing at the wrong section, which is the one defect in a chunk that
    no downstream stage can detect.
    """
    blocks = sorted(doc.blocks, key=lambda b: b.reading_order)
    current = Segment(heading=None)
    for block in blocks:
        if block.type is BlockType.HEADING:
            splits = block.attrs.get("unit") in split_on
            sibling = (block.level or 1) <= current.level
            if splits or sibling or current.heading is None:
                if current.heading is not None or current.blocks:
                    yield current
                current = Segment(heading=block)
                continue
        current.blocks.append(block)
    if current.heading is not None or current.blocks:
        yield current


def _groups(blocks: Sequence[Block], profile: Profile | None) -> list[Group]:
    """Split a segment at its sub-unit markers, keeping the sub-unit chain.

    ! A flat marker is an ambiguous locator, and an ambiguous locator is a citation
    that cannot be followed. Real regulations nest their lists: `2019pb3328073` has
    six separate `huruf a` inside one `Pasal 13`, each under a different `angka`. Named
    flatly they all read `Pasal 13 huruf a` — six different passages, one citation, and
    a reader sent to the wrong one with nothing to indicate it.

    Nesting is inferred from the markers themselves rather than from a declared depth,
    because a profile does not have to state one: a marker of kind K closes the last
    marker of kind K *and everything opened after it*, then opens its own. So
    `angka 3 → huruf a → huruf b → angka 4 → huruf a` yields
    `angka 3 huruf a`, `angka 3 huruf b`, `angka 4 huruf a`.
    """
    groups: list[Group] = [Group("")]
    stack: list[tuple[str, str]] = []
    for block in blocks:
        found = _subunit(block, profile)
        if found:
            kind, marker = found
            for index, (open_kind, _) in enumerate(stack):
                if open_kind == kind:
                    del stack[index:]
                    break
            stack.append((kind, marker))
            groups.append(Group(" ".join(m for _, m in stack)))
        groups[-1].blocks.append(block)
    return [g for g in groups if g.blocks]


def _subunit(block: Block, profile: Profile | None) -> tuple[str, str] | None:
    """`(kind, "kind marker")` for the sub-unit a block opens, or None.

    The kind comes from the profile's key (`ayat`) and the value from what its pattern
    matched (`(3)`), so `ayat (3)` is assembled without this file knowing either word.
    """
    if profile is None or block.type not in BODY_TYPES:
        return None
    found = profile.subunit(block.text.strip())
    if not found:
        return None
    kind, _value, marker = found
    return kind, f"{kind} {marker}"


def _lead(groups: Sequence[Group], config: ChunkConfig, builder: Builder) -> str:
    """The segment's own text, for carrying into each of its sub-units.

    Only when there *are* sub-units — otherwise the lead is already the chunk — and
    only when it is short enough to be a subject rather than a section. A long lead-in
    is content in its own right, and repeating it into every sub-unit produces a run
    of near-identical chunks whose real content is crowded out of the token budget.
    """
    if not config.carry_parent or len(groups) < 2 or groups[0].marker:
        return ""
    text = "\n\n".join(b.text.strip() for b in groups[0].blocks if b.text.strip())
    if builder.count_tokens(text) > config.carry_parent_max_tokens:
        return ""
    return text


def _is_body(block: Block, profile: Profile | None) -> bool:
    if block.type not in BODY_TYPES:
        return False
    text = block.text.strip()
    if not text:
        return False
    # 30,520 rows of the incumbent corpus are the single string `Cukup jelas.` —
    # thousands of near-identical vectors that distort ranking and answer no query.
    return not (profile and profile.is_boilerplate(text))


def _citation(doc: CanonicalDoc, profile: Profile | None) -> str | None:
    if profile is None:
        return None
    blocks = sorted(doc.blocks, key=lambda b: b.reading_order)[:CITATION_BLOCKS]
    text = "\n".join(b.text for b in blocks)
    return profile.cite(text)


def _identifier(citation: str | None, locator: str) -> str | None:
    if not citation:
        return None
    return f"{citation} {locator}".strip() if locator else citation


def _attrs(segment: Segment, group: Group) -> dict[str, str]:
    attrs: dict[str, str] = {}
    if segment.heading and (unit := segment.heading.attrs.get("unit")):
        attrs["unit"] = str(unit)
    if group.marker:
        # The innermost kind: `angka 3 huruf a` is a huruf.
        attrs["subunit"] = group.marker.rsplit(" ", 2)[-2]
    return attrs


# ---------------------------------------------------------------------------
# Minimum size
# ---------------------------------------------------------------------------
def _coalesce(plans: list[Plan], builder: Builder) -> list[Plan]:
    """Merge undersized plans into their neighbours.

    ! Merged before chunks are built, not after. Merging finished chunks would mean
    concatenating bodies and re-deriving provenance from rows that have already
    asserted it — exactly the after-the-fact repair `CHUNKING.md` §4.2 forbids. Merging
    plans keeps one chunk with one locator and the full set of `block_ids`, so a bad
    retrieval result still traces back to real bounding boxes.

    Undersized plans merge *forward* into the next; a trailing run merges *backward*
    into the last. Nothing is dropped — a one-line pasal that merely renumbers another
    is still part of the document, and discarding it to satisfy a size floor is
    `LOOPHOLES.md` §3 in miniature.
    """
    minimum = builder.config.min_tokens
    if minimum <= 0:
        return plans

    out: list[Plan] = []
    carry: list[Plan] = []
    for plan in plans:
        carry.append(plan)
        if builder.count_tokens(plan.preview(builder)) >= minimum:
            out.append(_join(carry))
            carry = []

    if carry:
        tail = _join(carry)
        if out:
            out[-1] = _join([out[-1], tail])
        else:
            out.append(tail)
    return out


def _join(plans: list[Plan]) -> Plan:
    """Several plans as one, keeping the first plan's identity.

    The first plan's locator wins because it is the outermost: a pasal absorbing its
    own short ayat is still that pasal, and a citation pointing at it resolves.
    """
    if len(plans) == 1:
        return plans[0]
    first = plans[0]
    blocks = [b for plan in plans for b in plan.blocks]
    leads = [plan for plan in plans if plan.lead]
    attrs = dict(first.attrs)
    attrs["merged"] = str(len(plans))
    return Plan(
        blocks=blocks,
        locator=first.locator,
        identifier=first.identifier,
        attrs=attrs,
        lead=leads[0].lead if leads else "",
        lead_blocks=leads[0].lead_blocks if leads else [],
    )
