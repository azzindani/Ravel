"""The canonical document — Ravel's intermediate representation.

Spec: docs/CANONICAL_FORMAT.md. This module is the single definition; the JSON
Schema, the parquet schema and the validator are all derived from it.

! `markdown` is a derived property, not a stored field. The spec sketch showed it
stored alongside `text`; storing both means two representations of one thing that
can silently disagree, which is the exact drift rule 5 of the validator exists to
catch. Render it, never persist it.
"""

from __future__ import annotations

import struct
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

CANON_VERSION = "1.0"

#: The block separator in canonical markdown (`canon.render.BLOCK_SEP`). Text that
#: contains it cannot survive a render/parse round trip as one block.
BLANK_LINE = "\n\n"


def _as_float32(value: float) -> float:
    """Snap to the precision the parquet column actually holds.

    ! Confidence is stored as float32 — at corpus scale the 4 bytes per block are
    real, and no extractor's confidence is meaningful past 7 digits. Snapping here
    rather than on write means the in-memory value equals the persisted value, so a
    shard round trip is exactly lossless instead of nearly so. Silent precision
    drift between memory and disk is the kind of difference that surfaces later as
    an unexplainable test failure.
    """
    rounded: float = struct.unpack("f", struct.pack("f", value))[0]
    return rounded


Confidence = Annotated[float, Field(ge=0.0, le=1.0), AfterValidator(_as_float32)]


class BlockType(StrEnum):
    """Closed set. A new document family gets a new `attrs` key, not a new type,
    unless the type is genuinely structural (CANONICAL_FORMAT.md §3)."""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE_REF = "table_ref"
    FIGURE_REF = "figure_ref"
    CAPTION = "caption"
    FOOTNOTE = "footnote"
    PAGE_HEADER = "page_header"
    PAGE_FOOTER = "page_footer"
    FORMULA = "formula"


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


class Source(Base):
    """The citable origin. `url` is the provenance root — never synthesized."""

    path: str
    url: str | None = None
    title: str
    mime: str
    bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Extraction(Base):
    """How this document was produced. Part of the cache key (EXECUTION.md §3)."""

    extractor: str
    extractor_version: str
    model: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    extracted_at: datetime
    pages: int | None = Field(default=None, ge=0)
    warnings: list[str] = Field(default_factory=list)


class Block(Base):
    """One unit of the document body, in reading order.

    The body is a flat ordered list, not a tree: `heading_path` is materialized on
    EVERY block so a chunker is a linear scan, never a tree walk.
    """

    id: str
    type: BlockType
    text: str = ""
    level: int | None = Field(default=None, ge=1, le=9)
    heading_path: list[str] = Field(default_factory=list)
    page: int | None = Field(default=None, ge=1)
    bbox: tuple[float, float, float, float] | None = None
    reading_order: int = Field(ge=0)
    confidence: Confidence | None = None
    attrs: dict[str, Any] = Field(default_factory=dict)
    # Set on TABLE_REF / FIGURE_REF only: the id of the table or asset it points at.
    ref: str | None = None

    @model_validator(mode="after")
    def _check_type_invariants(self) -> Self:
        # ! A blank line is the block separator in canonical markdown, so text
        # containing one round-trips as TWO blocks. Caught here at construction
        # rather than by the validator later, because by then the extractor that
        # produced it is long out of scope. Extractors must split instead.
        if BLANK_LINE in self.text:
            raise ValueError(
                f"block {self.id}: text contains a blank line, which is the block "
                "separator — emit separate blocks instead"
            )
        if self.type is BlockType.HEADING and self.level is None:
            raise ValueError(f"block {self.id}: heading requires a level")
        if self.type is not BlockType.HEADING and self.level is not None:
            raise ValueError(f"block {self.id}: level is meaningful only on headings")
        if self.type in (BlockType.TABLE_REF, BlockType.FIGURE_REF) and not self.ref:
            raise ValueError(f"block {self.id}: {self.type} requires `ref`")
        return self

    @property
    def markdown(self) -> str:
        from canon.render import render_block

        return render_block(self)


class Cell(Base):
    r: int = Field(ge=0)
    c: int = Field(ge=0)
    rowspan: int = Field(default=1, ge=1)
    colspan: int = Field(default=1, ge=1)
    text: str = ""


class Table(Base):
    """`cells` is the truth (spans preserved); `markdown` is the lossy rendering
    used for embedding and display. Code that needs correctness reads cells."""

    id: str
    page: int | None = Field(default=None, ge=1)
    bbox: tuple[float, float, float, float] | None = None
    caption: str | None = None
    n_rows: int = Field(ge=0)
    n_cols: int = Field(ge=0)
    header_rows: int = Field(default=0, ge=0)
    cells: list[Cell] = Field(default_factory=list)
    confidence: Confidence | None = None

    @model_validator(mode="after")
    def _check_bounds(self) -> Self:
        for cell in self.cells:
            if cell.r + cell.rowspan > self.n_rows or cell.c + cell.colspan > self.n_cols:
                raise ValueError(
                    f"table {self.id}: cell ({cell.r},{cell.c}) spans outside "
                    f"{self.n_rows}x{self.n_cols}"
                )
        return self

    @property
    def markdown(self) -> str:
        from canon.render import render_table

        return render_table(self)


class Asset(Base):
    """A figure or page raster. `recipe` regenerates it deterministically from the
    source rather than storing bytes (CANONICAL_FORMAT.md §7)."""

    id: str
    kind: str
    page: int | None = Field(default=None, ge=1)
    bbox: tuple[float, float, float, float] | None = None
    recipe: dict[str, Any] = Field(default_factory=dict)


class CanonicalDoc(Base):
    """One source document, extracted. The only interface between Phase A and B."""

    canon_version: str = CANON_VERSION
    doc_id: str
    source: Source
    extraction: Extraction
    blocks: list[Block] = Field(default_factory=list)
    tables: list[Table] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_doc_id(self) -> Self:
        # ! doc_id IS the source hash. The canonical doc is a derivation of the
        # source; its identity is the source's identity (CANONICAL_FORMAT.md §2).
        expected = f"sha256:{self.source.sha256}"
        if self.doc_id != expected:
            raise ValueError(f"doc_id {self.doc_id!r} must be {expected!r}")
        return self

    def ordered(self) -> list[Block]:
        return sorted(self.blocks, key=lambda b: b.reading_order)

    def table(self, table_id: str) -> Table | None:
        return next((t for t in self.tables if t.id == table_id), None)

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.ordered() if b.text)
