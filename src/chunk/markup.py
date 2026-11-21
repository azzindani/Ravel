"""Chunk a document whose markup states its structure and its identity.

The `unit` chunker reads one identity per document out of its opening text, which is right
for a regulation that is one file. A CFR volume is not: it holds dozens of parts from
several agencies, and stamping them all with the first one's number would make every
citation after the first part wrong. This chunker reads identity **per scope** from the
headings in force, through templates the profile declares (`identity.markup`), using only
what the extractor stated on each heading (`attrs`: `unit`, `number`, `name`, `marker`).
`SOURCE_US_CFR.md` §4.

What it does, in order:

1. Walk blocks in reading order, keeping the headings in force. The stack follows each
   block's materialized `heading_path`, so a unit the markup closed is closed here too.
2. Start a segment at every heading, and wherever the headings in force change.
3. Emit each segment's body as one chunk, split into `part n of m` over `max_tokens`, with
   the facts of its scope and the citation of its deepest cited unit; each table under it
   as a chunk of its own with the same identity.

! No coalescing across segments. Merging a short section into the next would give one chunk
two citations and keep one of them -- a locator pointing at the wrong section, which no
later stage can detect (`unit.py`, `_coalesce`). A one-line section is still the section.

! Nothing here knows a CFR word. The scope unit, the templates and the roles are the
profile's; pointed at another markup source it cites that source's units.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from canon import Block, BlockType, CanonicalDoc
from canon.render import render_table
from chunk.base import BODY_TYPES, Builder, chunker
from chunk.models import Chunk
from spec import Facts, Profile
from spec.models import MarkupIdentity

VERSION = "1.0"


@dataclass(slots=True)
class Segment:
    stack: tuple[Block, ...]
    blocks: list[Block] = field(default_factory=list)


@chunker("markup", version=VERSION)
def chunk_by_markup(doc: CanonicalDoc, builder: Builder) -> Iterator[Chunk]:
    """Per-scope identity from heading attributes; one chunk per cited unit."""
    profile = builder.profile
    # Without `identity.markup` the segments are the same and the chunks carry no identity:
    # a gap that can be counted, never one borrowed from the first part of the document.
    markup = profile.spec.identity.markup if profile is not None else None
    roles = {u.name: u.role for u in profile.units} if profile is not None else {}
    facts_cache: dict[str, Facts] = {}

    for segment in _segments(doc):
        body = [b for b in segment.blocks if _is_body(b, profile)]
        tables = [b for b in segment.blocks if b.type is BlockType.TABLE_REF and b.ref]
        if not body and not tables:
            continue
        in_force = {str(h.attrs["unit"]): h.attrs for h in segment.stack if h.attrs.get("unit")}
        if markup is not None:
            builder.facts = _facts(markup, segment.stack, in_force, facts_cache)
        identifier = _identifier(markup, segment.stack, in_force) if markup else None
        chapter = _label(segment.stack, roles, "chapter", key="text")
        article = _label(segment.stack, roles, "article", key="marker")
        deepest = segment.stack[-1] if segment.stack else None
        locator = deepest.text.strip().rstrip(":;.") if deepest is not None else None
        attrs = {"unit": str(deepest.attrs.get("unit"))} if deepest is not None else {}

        if body:
            yield from builder.split(
                body,
                locator=locator,
                identifier=identifier,
                attrs=attrs,
                chapter=chapter,
                article=article,
            )
        if builder.config.include_tables:
            for anchor in tables:
                table = doc.table(str(anchor.ref))
                grid = render_table(table).strip() if table is not None else ""
                if not grid:
                    continue
                chunk = builder.make(
                    [anchor],
                    body=grid,
                    locator=(table.caption if table and table.caption else locator),
                    identifier=identifier,
                    attrs={**attrs, "table_id": str(anchor.ref), "kind": "table"},
                    chapter=chapter,
                    article=article,
                )
                if chunk:
                    yield chunk


def _segments(doc: CanonicalDoc) -> Iterator[Segment]:
    """Blocks grouped by the headings in force over them, in reading order."""
    stack: list[Block] = []
    current: Segment | None = None
    for block in doc.ordered():
        is_heading = block.type is BlockType.HEADING
        parent = block.heading_path[:-1] if is_heading else block.heading_path
        # ! Follow the materialized path, not the levels: the extractor closes a unit
        # where its element ends, and the path is where that is recorded.
        while stack and (
            len(stack) > len(parent) or [h.text for h in stack] != parent[: len(stack)]
        ):
            stack.pop()
        if is_heading:
            stack.append(block)
        if current is None or [h.id for h in current.stack] != [h.id for h in stack]:
            if current is not None and current.blocks:
                yield current
            current = Segment(stack=tuple(stack))
        if not is_heading:
            current.blocks.append(block)
    if current is not None and current.blocks:
        yield current


def _is_body(block: Block, profile: Profile | None) -> bool:
    if block.type not in BODY_TYPES:
        return False
    text = block.text.strip()
    return bool(text) and not (profile is not None and profile.is_boilerplate(text))


def _facts(
    markup: MarkupIdentity,
    stack: tuple[Block, ...],
    in_force: dict[str, dict[str, Any]],
    cache: dict[str, Facts],
) -> Facts:
    """The facts of the scope in force, read once per scope heading."""
    scope = next((h for h in stack if h.attrs.get("unit") == markup.scope), None)
    key = scope.id if scope is not None else ""
    if key in cache:
        return cache[key]
    values = {name: MarkupIdentity.render(t, in_force) for name, t in markup.facts.items()}
    year = values.get("year")
    facts = Facts(
        regulation_type=values.get("regulation_type"),
        enacting_body=values.get("enacting_body"),
        regulation_number=values.get("regulation_number"),
        year=int(year) if year and year.isdigit() else None,
        about=values.get("about"),
        effective_date=values.get("effective_date"),
    )
    cache[key] = facts
    return facts


def _identifier(
    markup: MarkupIdentity, stack: tuple[Block, ...], in_force: dict[str, dict[str, Any]]
) -> str | None:
    """The citation of the deepest unit in force that has one.

    A unit whose template cannot be filled falls back to the next one out: `2 CFR Part 170`
    is a wider citation than the section's, and still true of the text.
    """
    for heading in reversed(stack):
        template = markup.identifier.get(str(heading.attrs.get("unit")))
        if template and (cited := MarkupIdentity.render(template, in_force)):
            return cited
    return None


def _label(
    stack: tuple[Block, ...], roles: dict[str, str | None], role: str, *, key: str
) -> str | None:
    """The innermost heading in force whose unit plays `role`, as a label."""
    for heading in reversed(stack):
        if roles.get(str(heading.attrs.get("unit"))) == role:
            value = heading.text if key == "text" else heading.attrs.get(key)
            return str(value).strip() if value else heading.text.strip()
    return None
