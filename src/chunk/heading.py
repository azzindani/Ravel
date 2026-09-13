"""Two general chunkers: heading-scoped, and a plain token budget.

`heading` is the default for structured documents whose profile declares no citable
unit — manuals, papers, scraped pages. `token` is the baseline every other chunker has
to beat; keeping it in the registry means "is our structure-aware chunking actually
worth it?" is a variant sweep rather than an argument (`EVAL.md`).
"""

from __future__ import annotations

from collections.abc import Iterator

from canon import Block, BlockType, CanonicalDoc
from chunk.base import BODY_TYPES, Builder, chunker
from chunk.models import PATH_SEP, Chunk

VERSION = "1.0"


@chunker("heading", version=VERSION)
def chunk_by_heading(doc: CanonicalDoc, builder: Builder) -> Iterator[Chunk]:
    """One chunk per heading scope, to a configurable depth.

    `params.depth` caps how deep a heading starts a new chunk; deeper headings stay
    inside their parent's chunk as ordinary text. Depth 0 means every heading splits.
    """
    depth = int(builder.config.params.get("depth", 0))
    blocks = sorted(doc.blocks, key=lambda b: b.reading_order)

    current: list[Block] = []
    path: list[str] = []

    def emit() -> Iterator[Chunk]:
        if current:
            locator = PATH_SEP.join(path) if path else None
            yield from builder.split(list(current), locator=locator)

    for block in blocks:
        if block.type is BlockType.HEADING and (not depth or (block.level or 1) <= depth):
            yield from emit()
            current = []
            path = [*block.heading_path, block.text.strip()]
            continue
        if block.type in BODY_TYPES and block.text.strip():
            current.append(block)

    yield from emit()
    yield from builder.tables()


@chunker("token", version=VERSION)
def chunk_by_token(doc: CanonicalDoc, builder: Builder) -> Iterator[Chunk]:
    """A flat token budget with heading carry — the baseline to beat.

    Deliberately structure-blind apart from recording the heading path it happens to
    be under. If a structure-aware chunker cannot beat this on the eval set, the
    structure work is not paying for itself and should be said so out loud.
    """
    blocks = [
        b
        for b in sorted(doc.blocks, key=lambda b: b.reading_order)
        if b.type in BODY_TYPES and b.text.strip()
    ]
    if blocks:
        yield from builder.split(blocks)
    yield from builder.tables()
