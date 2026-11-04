"""What a chunk says about the document it came from, and where in it the chunk sits.

Ported from the notebook that built the incumbent corpus, as data rather than code: the
profile declares the patterns and the roles, the engine reads them. These tests pin the
two halves separately -- `Profile.describe` for the document's own facts, and the unit
chunker for the `chapter` / `article` labels -- and then that both survive the chunk store
and reach the loader.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from canon import Block, BlockType, CanonicalDoc
from chunk import SCHEMA, Builder, ChunkConfig, get, read_chunks, write_chunks
from load.plan import CHUNK_COLUMNS
from spec import Profile, default_registry

REF = "id_regulation_md@1.0"

TITLE = (
    "PERATURAN BUPATI BANYUWANGI\n"
    "NOMOR 26 TAHUN 2011\n"
    "TENTANG\n"
    "PENJABARAN PERTANGGUNGJAWABAN PELAKSANAAN ANGGARAN\n"
    "DENGAN RAHMAT TUHAN YANG MAHA ESA\n"
    "BUPATI BANYUWANGI,\n"
    "Menimbang"
)


@pytest.fixture(scope="module")
def profile() -> Profile:
    return default_registry().get(REF)


# ---------------------------------------------------------------------------
# describe(): the document's own facts
# ---------------------------------------------------------------------------
def test_the_title_block_gives_type_body_number_year_and_subject(profile: Profile) -> None:
    facts = profile.describe(TITLE)
    assert facts.regulation_type == "PERATURAN BUPATI"
    assert facts.enacting_body == "BANYUWANGI"
    assert facts.regulation_number == "26"
    assert facts.year == 2011
    assert facts.about == "PENJABARAN PERTANGGUNGJAWABAN PELAKSANAAN ANGGARAN"


def test_a_regional_body_is_read_from_the_text_not_from_a_list(profile: Profile) -> None:
    facts = profile.describe("PERATURAN DAERAH KABUPATEN TANAH LAUT NOMOR 4 TAHUN 2015")
    assert (facts.regulation_type, facts.enacting_body) == (
        "PERATURAN DAERAH KABUPATEN",
        "TANAH LAUT",
    )


def test_what_cannot_be_read_is_none_never_unknown(profile: Profile) -> None:
    facts = profile.describe("sebuah dokumen tanpa judul yang dikenali")
    assert facts == type(facts)()


def test_a_subject_with_no_end_in_range_is_none_not_the_preamble(profile: Profile) -> None:
    # Nothing after TENTANG closes the subject, so the pattern refuses rather than
    # swallowing whatever follows into a column people filter on.
    head = "PERATURAN BUPATI X NOMOR 1 TAHUN 2010\nTENTANG\n" + "isi " * 200
    assert profile.describe(head).about is None


def test_the_effective_date_beats_the_issue_date(profile: Profile) -> None:
    text = (
        "Ditetapkan di Jakarta pada tanggal 18 April 2011\n"
        "Peraturan ini mulai berlaku pada tanggal 1 Januari 2012"
    )
    assert profile.describe("", text).effective_date == "1 Januari 2012"


def test_without_an_effective_date_the_issue_date_is_used(profile: Profile) -> None:
    text = "Ditetapkan di : Jakarta Pada tanggal : 3 Agustus 2011 PRESIDEN"
    assert profile.describe("", text).effective_date == "3 Agustus 2011"


def test_a_numeric_month_is_rendered_by_name(profile: Profile) -> None:
    assert profile.describe("", "Ditetapkan pada tanggal 18-04-2011").effective_date == (
        "18 April 2011"
    )


def test_a_date_that_does_not_validate_is_skipped_not_guessed(profile: Profile) -> None:
    # `Novehber` is OCR damage and `2Ol7` a letter-for-digit swap; neither is a date.
    assert profile.describe("", "Diundangkan di Ruteng pada tanggal 04 Novehber 2015") == (
        profile.describe("")
    )
    assert profile.describe("", "Diundangkan di Pamekasan pada tanggal 13 Oktober 2Ol7") == (
        profile.describe("")
    )


def test_a_number_that_already_carries_its_year_is_not_doubled(profile: Profile) -> None:
    facts = profile.describe("PERATURAN MENTERI KEUANGAN NOMOR 123/PMK.03/2019 TAHUN 2019")
    assert facts.regulation_number is not None
    assert not facts.regulation_number.endswith("/2019")


# ---------------------------------------------------------------------------
# the unit chunker: chapter and article labels
# ---------------------------------------------------------------------------
BODY = [
    (BlockType.PARAGRAPH, TITLE.replace("\n", " "), None, None),
    (BlockType.HEADING, "Menimbang", 3, "menimbang"),
    (
        BlockType.PARAGRAPH,
        "bahwa pertanggungjawaban pelaksanaan anggaran perlu dijabarkan.",
        None,
        None,
    ),
    (BlockType.HEADING, "BAB I", 3, "bab"),
    (BlockType.PARAGRAPH, "KETENTUAN UMUM", None, None),
    (BlockType.HEADING, "Pasal 1", 6, "pasal"),
    (
        BlockType.PARAGRAPH,
        "Laporan realisasi anggaran terdiri atas pendapatan dan belanja daerah.",
        None,
        None,
    ),
    (BlockType.HEADING, "BAB II KETENTUAN PENUTUP", 3, "bab"),
    (BlockType.HEADING, "Pasal5", 6, "pasal"),
    (
        BlockType.PARAGRAPH,
        "Peraturan ini mulai berlaku pada tanggal diundangkan oleh Bupati.",
        None,
        None,
    ),
    (BlockType.HEADING, "Ditetapkan di Banyuwangi pada tanggal 3 Agustus 2011", 2, "penutup"),
    (
        BlockType.PARAGRAPH,
        "BUPATI BANYUWANGI, ditandatangani oleh pejabat yang berwenang.",
        None,
        None,
    ),
    (BlockType.HEADING, "PENJELASAN ATAS", 2, "penjelasan"),
    (BlockType.HEADING, "Pasal 1", 6, "pasal"),
    (
        BlockType.PARAGRAPH,
        "Yang dimaksud dengan laporan realisasi adalah laporan tahunan daerah.",
        None,
        None,
    ),
    (BlockType.HEADING, "LAMPIRAN I PERATURAN BUPATI", 2, "lampiran"),
    (
        BlockType.PARAGRAPH,
        "Ringkasan laporan realisasi anggaran tahun anggaran 2010 terlampir.",
        None,
        None,
    ),
]


@pytest.fixture
def labelled(doc: CanonicalDoc) -> CanonicalDoc:
    built = doc.model_copy(deep=True)
    built.source.url = "hf://datasets/example/fixture/perbup.md"
    built.source.title = "perbup_26_2011"
    built.tables = []
    blocks: list[Block] = []
    stack: list[tuple[int, str]] = []
    for order, (btype, text, level, unit) in enumerate(BODY):
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
                attrs={"unit": unit} if unit else {},
            )
        )
    built.blocks = blocks
    return built


def _chunks(doc: CanonicalDoc, profile: Profile) -> list:  # noqa: ANN401
    entry = get("unit")
    builder = Builder(
        doc=doc,
        chunker=entry.id,
        version=entry.version,
        config=ChunkConfig(min_tokens=0),
        profile=profile,
    )
    return list(entry.fn(doc, builder))


def test_every_chunk_of_a_document_carries_the_documents_facts(
    labelled: CanonicalDoc, profile: Profile
) -> None:
    chunks = _chunks(labelled, profile)
    assert chunks
    for chunk in chunks:
        assert chunk.regulation_type == "PERATURAN BUPATI"
        assert chunk.enacting_body == "BANYUWANGI"
        assert (chunk.regulation_number, chunk.year) == ("26", 2011)
        assert chunk.effective_date == "3 Agustus 2011"


def test_chapter_and_article_follow_the_documents_headings(
    labelled: CanonicalDoc, profile: Profile
) -> None:
    where = {(c.chapter, c.article): c.body[:20] for c in _chunks(labelled, profile)}
    # The title block precedes every marker, and the profile calls that Pembukaan.
    assert ("N/A", "Pembukaan") in where
    # A title under a marker-only chapter heading is taken from the line below it.
    assert ("BAB I - KETENTUAN UMUM", "Pasal 1") in where
    # A title on the marker's own line, and a glued `Pasal5` restored to `Pasal 5`.
    assert ("BAB II - KETENTUAN PENUTUP", "Pasal 5") in where
    # The signing block opens the PENUTUP region and is labelled Pengesahan.
    assert ("PENUTUP", "Pengesahan") in where
    # A region replaces the chapter: the explanation's Pasal 1 is not BAB II's.
    assert ("PENJELASAN", "Pasal 1") in where
    assert ("LAMPIRAN", "LAMPIRAN") in where


def test_a_document_without_a_profile_gets_no_labels_and_no_facts(
    labelled: CanonicalDoc,
) -> None:
    entry = get("unit")
    builder = Builder(
        doc=labelled, chunker=entry.id, version=entry.version, config=ChunkConfig()
    )
    for chunk in entry.fn(labelled, builder):
        assert chunk.chapter is None
        assert chunk.article is None
        assert chunk.regulation_type is None


def test_a_chapter_inside_a_region_is_named_for_both(
    labelled: CanonicalDoc, profile: Profile
) -> None:
    # Reuse the fixture with a BAB inserted under PENJELASAN: the explanation of a long
    # regulation repeats its chapters.
    blocks = list(labelled.blocks)
    index = next(i for i, b in enumerate(blocks) if b.text == "PENJELASAN ATAS") + 1
    shifted = [
        b.model_copy(update={"reading_order": b.reading_order + 1}) for b in blocks[index:]
    ]
    inserted = Block(
        id="bX",
        type=BlockType.HEADING,
        text="BAB I KETENTUAN UMUM",
        level=3,
        heading_path=["PENJELASAN ATAS", "BAB I KETENTUAN UMUM"],
        page=1,
        reading_order=blocks[index].reading_order,
        attrs={"unit": "bab"},
    )
    labelled.blocks = [*blocks[:index], inserted, *shifted]
    chapters = {c.chapter for c in _chunks(labelled, profile)}
    assert "PENJELASAN - BAB I - KETENTUAN UMUM" in chapters


# ---------------------------------------------------------------------------
# the chunk store and the loader carry them
# ---------------------------------------------------------------------------
def test_the_facts_survive_the_chunk_store(
    labelled: CanonicalDoc, profile: Profile, tmp_path: Path
) -> None:
    chunks = _chunks(labelled, profile)
    path = str(tmp_path / "chunks.parquet")
    write_chunks(chunks, path)
    assert read_chunks(path) == chunks


def test_every_column_the_loader_stages_is_in_the_chunk_shard() -> None:
    # The loader's COPY names columns; a name the shard lacks is a load that fails on its
    # first row, found by the one person with a database to try it on.
    assert set(CHUNK_COLUMNS) <= set(SCHEMA.names)
    for column in ("regulation_type", "enacting_body", "regulation_number", "year", "about"):
        assert column in CHUNK_COLUMNS
    for column in ("effective_date", "chapter", "article"):
        assert column in CHUNK_COLUMNS
