"""Chunking: provenance, determinism, and the structural split.

These assert the properties `CHUNKING.md` says are non-negotiable. A chunk that
retrieves well but cites wrongly is worse than no chunk, so most of what is checked
here is provenance rather than segmentation quality — quality is measured on the real
corpus by the eval set, correctness is asserted here.
"""

from __future__ import annotations

import pytest

from canon import Block, BlockType, CanonicalDoc
from chunk import Builder, ChunkConfig, available, chunk_id, get
from spec import default_registry

REGULATION = [
    # The title is a block, because that is where a real document carries it. The
    # citation is read from the document's own text, never from its filename.
    (BlockType.PARAGRAPH, "PERATURAN BUPATI BOYOLALI NOMOR 15 TAHUN 2011 TENTANG PAJAK", None),
    (BlockType.HEADING, "Menimbang", 3),
    (BlockType.PARAGRAPH, "bahwa untuk melaksanakan ketentuan tersebut perlu diatur.", None),
    (BlockType.HEADING, "BAB I", 3),
    (BlockType.HEADING, "Pasal 1", 6),
    (BlockType.PARAGRAPH, "Dalam Peraturan ini yang dimaksud dengan:", None),
    (BlockType.PARAGRAPH, "(1) Daerah adalah Kabupaten Boyolali sebagaimana dimaksud.", None),
    (BlockType.PARAGRAPH, "(2) Bupati adalah Bupati Boyolali yang memimpin daerah.", None),
    (BlockType.HEADING, "Pasal 2", 6),
    (BlockType.PARAGRAPH, "Wajib Pajak menyelenggarakan pembukuan sesuai ketentuan.", None),
    (BlockType.PARAGRAPH, "Pembukuan tersebut disimpan selama sepuluh tahun.", None),
]


@pytest.fixture
def regulation(doc: CanonicalDoc) -> CanonicalDoc:
    """A small regulation with the structure the profile expects."""
    built = doc.model_copy(deep=True)
    built.source.url = "hf://datasets/example/fixture/perbup.pdf"
    built.source.title = "perbup_15_2011"  # a filename, as extraction records it
    built.extraction.params["profile"] = "id_regulation@1.0"
    built.tables = []

    blocks: list[Block] = []
    stack: list[tuple[int, str]] = []
    for order, (btype, text, level) in enumerate(REGULATION):
        if btype is BlockType.HEADING:
            while stack and stack[-1][0] >= (level or 1):
                stack.pop()
            stack.append((level or 1, text))
        blocks.append(
            Block(
                id=f"b{order:05d}",
                type=btype,
                text=text,
                level=level,
                heading_path=[t for _, t in stack],
                page=1,
                reading_order=order,
                attrs={"unit": _unit_of(text)} if btype is BlockType.HEADING else {},
            )
        )
    built.blocks = blocks
    return built


def _unit_of(text: str) -> str:
    lowered = text.lower()
    if lowered.startswith("pasal"):
        return "pasal"
    if lowered.startswith("bab"):
        return "bab"
    return "menimbang"


def build(doc: CanonicalDoc, **kw: object):  # noqa: ANN201
    entry = get(str(kw.pop("chunker", "unit")))
    config = ChunkConfig(**kw)  # type: ignore[arg-type]
    builder = Builder(
        doc=doc,
        chunker=entry.id,
        version=entry.version,
        config=config,
        profile=default_registry().get("id_regulation"),
    )
    return list(entry.fn(doc, builder))


# ===========================================================================
# Provenance — the part that cannot be repaired later
# ===========================================================================
def test_every_chunk_carries_full_provenance(regulation: CanonicalDoc) -> None:
    """! The defect this whole stage exists to prevent: all 748,558 rows of the
    incumbent corpus have no source_url, and a citation cannot be back-filled."""
    for chunk in build(regulation):
        assert chunk.source_url.startswith("hf://")
        assert chunk.source_title
        assert chunk.source_sha256 == regulation.source.sha256
        assert chunk.doc_id == regulation.doc_id
        assert chunk.block_ids


def test_a_document_without_a_url_cannot_be_chunked(regulation: CanonicalDoc) -> None:
    """Never synthesized (`CHUNKING.md` §4.1). A plausible URL that was never verified
    is worse than a missing one, because it looks like a citation."""
    orphan = regulation.model_copy(deep=True)
    orphan.source.url = None

    with pytest.raises(ValueError, match="no source_url"):
        build(orphan)


def test_block_ids_trace_back_into_the_canonical_document(regulation: CanonicalDoc) -> None:
    known = {b.id for b in regulation.blocks}
    for chunk in build(regulation):
        assert set(chunk.block_ids) <= known


# ===========================================================================
# Determinism
# ===========================================================================
def test_chunking_is_deterministic(regulation: CanonicalDoc) -> None:
    assert [c.id for c in build(regulation)] == [c.id for c in build(regulation)]


def test_changing_the_config_changes_every_id(regulation: CanonicalDoc) -> None:
    """! Two variants of one corpus must occupy disjoint id spaces, or a bundle built
    from a mix of them is unverifiable (`LOOPHOLES.md` §1)."""
    a = {c.id for c in build(regulation, max_tokens=512)}
    b = {c.id for c in build(regulation, max_tokens=256)}
    assert a.isdisjoint(b)


def test_changing_the_body_changes_the_id() -> None:
    """An edited chunk is a NEW chunk. The old id cannot be quietly rewritten under a
    citation someone has already recorded."""
    args = {
        "doc_id": "sha256:" + "0" * 64,
        "chunker": "unit",
        "version": "1.0",
        "config_hash": "abc",
        "locator": "Pasal 1",
    }
    assert chunk_id(body="a", **args) != chunk_id(body="b", **args)


def test_the_id_cannot_be_confused_by_field_boundaries() -> None:
    """Fields are NUL-separated, so ("ab","c") and ("a","bc") cannot hash alike."""
    base = {"doc_id": "d", "version": "1.0", "config_hash": "h", "body": "x"}
    assert chunk_id(chunker="ab", locator="c", **base) != chunk_id(
        chunker="a", locator="bc", **base
    )


# ===========================================================================
# The structural split
# ===========================================================================
def test_it_splits_on_the_profiles_deepest_unit(regulation: CanonicalDoc) -> None:
    sections = [c.locator_section for c in build(regulation)]
    assert "Pasal 1 ayat (1)" in sections
    assert "Pasal 2" in sections


def test_a_sub_unit_carries_its_parents_lead_in(regulation: CanonicalDoc) -> None:
    """An ayat is routinely meaningless alone — the pasal's opening line carries the
    subject the ayat only qualifies."""
    ayat = next(c for c in build(regulation) if c.locator_section == "Pasal 1 ayat (1)")
    assert ayat.body.startswith("Dalam Peraturan ini yang dimaksud dengan:")
    assert "Daerah adalah Kabupaten Boyolali" in ayat.body


def test_a_long_lead_in_is_not_carried(regulation: CanonicalDoc) -> None:
    """! Without a cap, a section whose lead-in is its whole enumerated list was
    prepended to all 40 of its sub-units, and the real content was pushed out of the
    token budget by a preamble repeated 40 times."""
    lead = "Dalam Peraturan ini yang dimaksud dengan:"
    carried = build(regulation)
    capped = build(regulation, carry_parent_max_tokens=2)

    assert sum(lead in c.body for c in carried) == 2  # once per ayat, by design
    assert sum(lead in c.body for c in capped) == 1  # kept, but not repeated


def test_the_identifier_is_the_documents_own_citation(regulation: CanonicalDoc) -> None:
    """This is what Vera's exact-match bypass keys on, so it must be the citation a
    human would actually write."""
    pasal = next(c for c in build(regulation) if c.locator_section == "Pasal 2")
    assert pasal.identifier == "Perbup 15/2011 Pasal 2"


def test_a_document_the_profile_cannot_cite_gets_no_identifier(
    regulation: CanonicalDoc,
) -> None:
    """None rather than a guess. An identifier that is wrong is worse than one that is
    absent, because the exact-match path trusts it completely."""
    anonymous = regulation.model_copy(deep=True)
    anonymous.blocks[0].text = "Sesuatu yang bukan peraturan sama sekali"

    assert all(c.identifier is None for c in build(anonymous))


def test_siblings_do_not_absorb_each_other(regulation: CanonicalDoc) -> None:
    """! A locator pointing at the wrong section is the one defect in a chunk that no
    downstream stage can detect. `Mengingat` was being absorbed into the `Menimbang`
    segment, so its chunks went out labelled with a section they were not in."""
    for chunk in build(regulation):
        if chunk.locator_section and chunk.heading_path:
            head = chunk.locator_section.split(" ayat")[0].split(" huruf")[0]
            assert head in chunk.heading_path


# ===========================================================================
# Size
# ===========================================================================
def test_an_oversized_unit_is_split_into_parts_not_truncated(
    regulation: CanonicalDoc,
) -> None:
    chunks = build(regulation, max_tokens=12, min_tokens=0)
    parts = [c for c in chunks if c.part[1] > 1]
    assert parts
    assert {c.part[0] for c in parts} == set(range(1, parts[0].part[1] + 1))
    joined = " ".join(c.body for c in chunks)
    assert "Wajib Pajak menyelenggarakan pembukuan" in joined


def test_undersized_chunks_are_merged_not_dropped(regulation: CanonicalDoc) -> None:
    """Nothing is discarded to satisfy a size floor — that is `LOOPHOLES.md` §3 in
    miniature. A one-line pasal is still part of the document."""
    generous = build(regulation, min_tokens=1000)
    text = " ".join(c.body for c in generous)
    assert "Wajib Pajak" in text
    assert "Daerah adalah Kabupaten Boyolali" in text


def test_no_chunk_has_an_empty_body(regulation: CanonicalDoc) -> None:
    assert all(c.body.strip() for c in build(regulation))


# ===========================================================================
# The registry
# ===========================================================================
def test_every_registered_chunker_runs_on_a_real_document(
    regulation: CanonicalDoc,
) -> None:
    """A chunker that is registered but broken fails at corpus scale, hours in."""
    for entry in available():
        chunks = build(regulation, chunker=entry.id)
        assert chunks, f"{entry.ref} produced nothing"
        assert all(c.chunker == entry.id for c in chunks)


def test_an_unknown_chunker_names_the_known_ones() -> None:
    with pytest.raises(KeyError, match="unit"):
        get("nope")


def test_a_carried_lead_in_stays_traceable_to_its_block(regulation: CanonicalDoc) -> None:
    """! The debugging path must not break exactly where it is most needed. A reader
    questioning the first sentence of a chunk has to be able to find the block it came
    from, and a carried lead-in is not in the chunk's own block group."""
    lead_block = next(b for b in regulation.blocks if b.text.startswith("Dalam Peraturan ini"))
    ayat = next(c for c in build(regulation) if c.locator_section == "Pasal 1 ayat (1)")

    assert lead_block.id in ayat.block_ids
    assert ayat.block_ids[0] == lead_block.id


def test_repeated_text_at_one_locator_gets_distinct_ids(regulation: CanonicalDoc) -> None:
    """! 710 of 121,757 real chunks collided without this.

    The `CHUNKING.md` §6 formula assumed a document never repeats short content at the
    same locator, and real ones do constantly — `KETENTUAN PENUTUP` under `BAB III`, a
    tariff row under `Pasal 16 huruf a`. Vera keys on the chunk id, so each collision
    would have dropped or overwritten a row with no count ever looking wrong.
    """
    from canon import Block, BlockType

    doc = regulation.model_copy(deep=True)
    order = len(doc.blocks)
    for _ in range(2):
        doc.blocks.append(
            Block(
                id=f"b{order:05d}",
                type=BlockType.PARAGRAPH,
                text="KETENTUAN PENUTUP",
                heading_path=["BAB I", "Pasal 2"],
                page=1,
                reading_order=order,
            )
        )
        order += 1

    ids = [c.id for c in build(doc, min_tokens=0)]
    assert len(ids) == len(set(ids))


def test_every_chunk_of_a_document_has_a_unique_id(regulation: CanonicalDoc) -> None:
    ids = [c.id for c in build(regulation)]
    assert len(ids) == len(set(ids))


def test_nested_sub_units_produce_an_unambiguous_locator(regulation: CanonicalDoc) -> None:
    """! An ambiguous locator is a citation that cannot be followed.

    `2019pb3328073` has six separate `huruf a` inside one `Pasal 13`, each under a
    different `angka`. Named flatly they all read `Pasal 13 huruf a` — six passages,
    one citation, and a reader sent to the wrong one with nothing to indicate it.
    """
    from canon import Block, BlockType

    doc = regulation.model_copy(deep=True)
    doc.blocks = [b for b in doc.blocks if not b.text.startswith(("(1)", "(2)"))]
    order = max(b.reading_order for b in doc.blocks) + 1
    lines = [
        "1. Bidang pertama meliputi hal berikut ini secara lengkap dan menyeluruh.",
        "a. penyusunan rencana kerja tahunan bidang tersebut.",
        "b. pelaksanaan evaluasi atas rencana kerja tahunan tersebut.",
        "2. Bidang kedua meliputi hal berikut ini secara lengkap dan menyeluruh.",
        "a. penyusunan rencana kerja tahunan bidang tersebut.",
    ]
    for text in lines:
        doc.blocks.append(
            Block(
                id=f"b{order:05d}",
                type=BlockType.PARAGRAPH,
                text=text,
                heading_path=["BAB I", "Pasal 2"],
                page=1,
                reading_order=order,
            )
        )
        order += 1

    sections = [c.locator_section for c in build(doc, min_tokens=0)]
    assert "Pasal 2 angka 1 huruf a" in sections
    assert "Pasal 2 angka 2 huruf a" in sections
    assert "Pasal 2 huruf a" not in sections
