"""Canonical markdown: render blocks out, parse them back.

! This is a DIALECT, not general markdown. Every block type must survive
render -> parse -> render unchanged, because validator rule 5 uses that round trip
to catch renderer/parser drift (CANONICAL_FORMAT.md §6). Markdown alone cannot
express a page footer or a caption, so those carry an explicit annotation. Ugly
beats ambiguous: an ambiguous dialect silently reclassifies blocks.
"""

from __future__ import annotations

import re

from canon.models import Block, BlockType, Table

# Types markdown expresses natively; everything else gets an annotation comment.
_ANNOTATED = {
    BlockType.CAPTION,
    BlockType.FOOTNOTE,
    BlockType.PAGE_HEADER,
    BlockType.PAGE_FOOTER,
}

_ANNOTATION_RE = re.compile(r"^<!--\s*ravel:(?P<kind>[a-z_]+)\s*-->\s*(?P<text>.*)$", re.S)
_REF_RE = re.compile(r"^<!--\s*ravel:(?P<kind>table|figure)\s+(?P<ref>\S+)\s*-->$")
_HEADING_RE = re.compile(r"^(?P<hashes>#{1,9})\s+(?P<text>.*)$", re.S)
_LIST_RE = re.compile(r"^-\s+(?P<text>.*)$", re.S)
_FORMULA_RE = re.compile(r"^\$\$\s*(?P<text>.*?)\s*\$\$$", re.S)

#: A paragraph whose text opens with one of these would re-parse as another block
#: type. It is escaped with a leading backslash on render and unescaped on parse.
#:
#: ! This is not hypothetical tidiness. Indonesian regulations routinely open a line
#: with "- ", and without escaping those documents round-trip as LIST_ITEM and are
#: rejected by validator rule 5 — 8 of the first 40 real PDFs failed exactly this way.
_AMBIGUOUS_PREFIX = re.compile(r"^(?:\\|#{1,9}\s|-\s|\$\$|<!--)")
ESCAPE = "\\"

BLOCK_SEP = "\n\n"


def render_block(block: Block) -> str:
    """One block as canonical markdown."""
    match block.type:
        case BlockType.HEADING:
            return f"{'#' * (block.level or 1)} {block.text}"
        case BlockType.PARAGRAPH:
            return escape_paragraph(block.text)
        case BlockType.LIST_ITEM:
            return f"- {block.text}"
        case BlockType.FORMULA:
            return f"$$ {block.text} $$"
        case BlockType.TABLE_REF:
            return f"<!-- ravel:table {block.ref} -->"
        case BlockType.FIGURE_REF:
            return f"<!-- ravel:figure {block.ref} -->"
        case _:
            return f"<!-- ravel:{block.type.value} -->{block.text}"


def escape_paragraph(text: str) -> str:
    """Neutralize a leading marker so a paragraph stays a paragraph."""
    return ESCAPE + text if _AMBIGUOUS_PREFIX.match(text) else text


def parse_block(raw: str) -> tuple[BlockType, str, int | None, str | None]:
    """Inverse of `render_block`: (type, text, level, ref).

    Deliberately total — unrecognized input is a paragraph, never an error, so a
    hand-edited document degrades instead of failing.
    """
    raw = raw.strip()

    # ! Checked before every other rule. An escaped paragraph is a paragraph no
    # matter what it opens with, which is what makes the dialect unambiguous.
    if raw.startswith(ESCAPE):
        return BlockType.PARAGRAPH, raw[len(ESCAPE) :], None, None

    if m := _REF_RE.match(raw):
        kind = m.group("kind")
        btype = BlockType.TABLE_REF if kind == "table" else BlockType.FIGURE_REF
        return btype, "", None, m.group("ref")

    if m := _ANNOTATION_RE.match(raw):
        kind = m.group("kind")
        if kind in {t.value for t in _ANNOTATED}:
            return BlockType(kind), m.group("text").strip(), None, None

    if m := _HEADING_RE.match(raw):
        return BlockType.HEADING, m.group("text").strip(), len(m.group("hashes")), None

    if m := _FORMULA_RE.match(raw):
        return BlockType.FORMULA, m.group("text").strip(), None, None

    if m := _LIST_RE.match(raw):
        return BlockType.LIST_ITEM, m.group("text").strip(), None, None

    return BlockType.PARAGRAPH, raw, None, None


def render_blocks(blocks: list[Block]) -> str:
    return BLOCK_SEP.join(render_block(b) for b in blocks)


def parse_blocks(markdown: str) -> list[tuple[BlockType, str, int | None, str | None]]:
    return [parse_block(chunk) for chunk in markdown.split(BLOCK_SEP) if chunk.strip()]


def render_table(table: Table) -> str:
    """A table as a markdown grid.

    Lossy by design — spans are flattened, and `Table.cells` remains the truth.
    A spanned cell's text is written into its origin position only; the covered
    positions are left empty rather than duplicated, so the text is not counted
    twice by anything that embeds this.
    """
    if table.n_rows == 0 or table.n_cols == 0:
        return ""

    grid = [["" for _ in range(table.n_cols)] for _ in range(table.n_rows)]
    for cell in table.cells:
        grid[cell.r][cell.c] = cell.text.replace("|", r"\|").replace("\n", " ").strip()

    def row(cells: list[str]) -> str:
        return "| " + " | ".join(cells) + " |"

    # ! Respect header_rows == 0. Promoting the first data row to a header states
    # something about the table that is not true, and whatever embeds this would
    # read a data value as a column name.
    header_rows = min(table.header_rows, table.n_rows)
    lines = [row(grid[r]) for r in range(header_rows)]
    lines.append("| " + " | ".join(["---"] * table.n_cols) + " |")
    lines += [row(grid[r]) for r in range(header_rows, table.n_rows)]

    if table.caption:
        lines.insert(0, f"**{table.caption}**")
    return "\n".join(lines)
