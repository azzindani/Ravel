"""The canonical format's contract tests.

The round-trip test (CANONICAL_FORMAT.md §6 rule 5) is the critical one: it is what
catches renderer/parser drift, which would silently reclassify blocks.
"""

from __future__ import annotations

import pytest
from conftest import block
from pydantic import ValidationError

from canon import (
    Block,
    BlockType,
    CanonicalDoc,
    Cell,
    Severity,
    Table,
    is_valid,
    parse_blocks,
    render_blocks,
    validate,
)


def errors(doc: CanonicalDoc) -> list[str]:
    return [i.rule for i in validate(doc) if i.severity is Severity.ERROR]


# -- the fixture is the reference ------------------------------------------
def test_fixture_is_valid(doc: CanonicalDoc) -> None:
    assert is_valid(doc), [str(i) for i in validate(doc)]


def test_doc_id_must_be_the_source_hash(doc: CanonicalDoc) -> None:
    with pytest.raises(ValidationError, match="doc_id"):
        doc.model_copy(update={"doc_id": "sha256:" + "0" * 64}).model_validate(
            doc.model_dump() | {"doc_id": "sha256:" + "0" * 64}
        )


# -- rule 5: markdown round trip -------------------------------------------
def test_round_trip_preserves_every_block(doc: CanonicalDoc) -> None:
    ordered = doc.ordered()
    reparsed = parse_blocks(render_blocks(ordered))

    assert len(reparsed) == len(ordered)
    for original, (btype, text, level, ref) in zip(ordered, reparsed, strict=True):
        assert btype is original.type, f"{original.id}: {original.type} -> {btype}"
        assert text == original.text.strip(), original.id
        assert level == original.level
        assert ref == original.ref


@pytest.mark.parametrize(
    ("btype", "text", "level"),
    [
        (BlockType.HEADING, "Pasal 9", 3),
        (BlockType.PARAGRAPH, "- bukan list, hanya teks yang diawali tanda hubung", None),
        (BlockType.LIST_ITEM, "a. huruf pertama", None),
        (BlockType.CAPTION, "Tarif", None),
        (BlockType.PAGE_FOOTER, "Halaman 17", None),
        (BlockType.FOOTNOTE, "Lihat Pasal 4.", None),
        (BlockType.FORMULA, "x = y", None),
    ],
)
def test_every_type_survives_a_round_trip(
    btype: BlockType, text: str, level: int | None
) -> None:
    b = Block(id="b1", type=btype, text=text, level=level, reading_order=0)
    parsed_type, parsed_text, parsed_level, _ = parse_blocks(render_blocks([b]))[0]

    assert parsed_type is btype
    assert parsed_text == text
    assert parsed_level == level


@pytest.mark.parametrize(
    "text",
    [
        "- Mengingat Undang-Undang Nomor 28",  # would re-parse as LIST_ITEM
        "# not a heading",  # would re-parse as HEADING
        "$$ not a formula",  # would re-parse as FORMULA
        "<!-- ravel:caption -->not an annotation",
        chr(92) + "already starts with a backslash",
        "-tanpa spasi",
    ],
)
def test_a_paragraph_that_looks_like_a_marker_stays_a_paragraph(text: str) -> None:
    """! Not a hypothetical. Indonesian regulations routinely open a line with
    "- ", and before paragraphs were escaped this rejected 8 of the first 40 real
    PDFs through validator rule 5 — the dialect claimed an ambiguity was acceptable
    and the corpus disagreed."""
    b = Block(id="b1", type=BlockType.PARAGRAPH, text=text, reading_order=0)
    parsed_type, parsed_text, _, _ = parse_blocks(render_blocks([b]))[0]

    assert parsed_type is BlockType.PARAGRAPH
    assert parsed_text == text


# -- rule 2: reading order --------------------------------------------------
def test_reading_order_gap_is_an_error(doc: CanonicalDoc) -> None:
    doc.blocks[-1].reading_order = 99
    assert "reading_order" in errors(doc)


def test_duplicate_reading_order_is_an_error(doc: CanonicalDoc) -> None:
    doc.blocks[-1].reading_order = doc.blocks[0].reading_order
    assert "reading_order" in errors(doc)


# -- rule 3: table refs -----------------------------------------------------
def test_dangling_table_ref_is_an_error(doc: CanonicalDoc) -> None:
    doc.tables.clear()
    assert "table_refs" in errors(doc)


def test_unreferenced_table_is_an_error(doc: CanonicalDoc) -> None:
    """A table nobody points at has no position in reading order — a chunker
    cannot know where it belongs."""
    doc.blocks = [b for b in doc.blocks if b.type is not BlockType.TABLE_REF]
    for i, b in enumerate(doc.ordered()):
        b.reading_order = i
    assert "table_refs" in errors(doc)


# -- rule 4: heading path ---------------------------------------------------
def test_wrong_heading_path_is_an_error(doc: CanonicalDoc) -> None:
    doc.blocks[5].heading_path = ["UU 28/2007"]
    assert "heading_path" in errors(doc)


def test_heading_path_tracks_level_pops(doc: CanonicalDoc) -> None:
    """Closing a Bab must pop Pasal off the path, not leave it dangling."""
    root = "UU 28/2007"
    doc.blocks.append(block(13, BlockType.HEADING, "Bab III", level=2, path=[root, "Bab III"]))
    assert "heading_path" not in errors(doc)


# -- rule 6: silent extraction failure --------------------------------------
def test_empty_extraction_is_an_error(doc: CanonicalDoc) -> None:
    for b in doc.blocks:
        b.text = ""
    assert "text_ratio" in errors(doc)


def test_no_blocks_at_all_is_an_error(doc: CanonicalDoc) -> None:
    doc.blocks.clear()
    doc.tables.clear()
    assert "text_ratio" in errors(doc)


# -- model invariants -------------------------------------------------------
def test_heading_requires_a_level() -> None:
    with pytest.raises(ValidationError, match="requires a level"):
        Block(id="b1", type=BlockType.HEADING, text="x", reading_order=0)


def test_level_is_meaningless_off_a_heading() -> None:
    with pytest.raises(ValidationError, match="only on headings"):
        Block(id="b1", type=BlockType.PARAGRAPH, text="x", level=2, reading_order=0)


def test_table_ref_requires_a_target() -> None:
    with pytest.raises(ValidationError, match="requires `ref`"):
        Block(id="b1", type=BlockType.TABLE_REF, reading_order=0)


def test_cell_outside_the_grid_is_rejected() -> None:
    with pytest.raises(ValidationError, match="spans outside"):
        Table(id="t1", n_rows=2, n_cols=2, cells=[Cell(r=1, c=1, colspan=2, text="x")])


# -- table rendering --------------------------------------------------------
def test_table_markdown_keeps_spans_from_duplicating_text(doc: CanonicalDoc) -> None:
    md = doc.tables[0].markdown
    # The header cell spans both columns; its text appears exactly once, in its
    # origin position — the covered cell stays empty so nothing embeds it twice.
    assert md.count("Lapisan Penghasilan") == 1
    assert md.startswith("**Tarif Pajak Penghasilan**")
    assert "| --- | --- |" in md
    assert "5%" in md and "15%" in md


# -- regressions ------------------------------------------------------------
def test_blank_line_in_block_text_is_rejected() -> None:
    """! A blank line is the block separator: such text round-trips as TWO blocks.
    Caught at construction, because by validation time the extractor that produced
    it is long out of scope."""
    with pytest.raises(ValidationError, match="blank line"):
        Block(id="b1", type=BlockType.PARAGRAPH, text="first\n\nsecond", reading_order=0)


def test_single_newline_in_block_text_is_fine() -> None:
    b = Block(id="b1", type=BlockType.PARAGRAPH, text="wrapped\nline", reading_order=0)
    assert parse_blocks(render_blocks([b]))[0][1] == "wrapped\nline"


def test_table_without_a_header_does_not_get_one() -> None:
    """Promoting the first data row to a header states something untrue about the
    table, and whatever embeds it reads a data value as a column name."""
    t = Table(
        id="t1",
        n_rows=2,
        n_cols=2,
        header_rows=0,
        cells=[
            Cell(r=0, c=0, text="data A"),
            Cell(r=0, c=1, text="1"),
            Cell(r=1, c=0, text="data B"),
            Cell(r=1, c=1, text="2"),
        ],
    )
    lines = t.markdown.splitlines()
    assert lines[0] == "| --- | --- |"
    assert "data A" in lines[1]


def test_the_validator_rejects_a_path_that_nests_siblings(doc: CanonicalDoc) -> None:
    """! A validator that replays how a value was built cannot catch a bug in that
    construction. This one asserts the invariant instead — an ancestor is shallower —
    which is what makes it an independent check rather than a second copy."""
    from canon import Block, BlockType, Severity, validate

    bad = doc.model_copy(deep=True)
    bad.blocks = [
        Block(
            id="b00000",
            type=BlockType.HEADING,
            text="Menimbang",
            level=3,
            heading_path=["Menimbang"],
            reading_order=0,
        ),
        Block(
            id="b00001",
            type=BlockType.HEADING,
            text="Mengingat",
            level=3,
            heading_path=["Menimbang", "Mengingat"],
            reading_order=1,
        ),
    ]
    bad.tables = []

    errors = [i for i in validate(bad) if i.severity is Severity.ERROR]
    assert any(i.rule == "heading_path" for i in errors)
