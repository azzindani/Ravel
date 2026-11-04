"""`id_regulation_md` · the profile for the Docling-style Markdown export of regulations.

Every case here is a line, or a shape of line, that was read in `Azzindani/ID_REG_Markdown`
(46,457 files; census in `docs/SOURCE_ID_REG_MARKDOWN.md`). The near-misses are the ones that
look like structure and are not, which is the half of a profile that fails silently.

Two things are pinned on purpose:

* **The documented gaps.** `Menimbang : a. bahwa ...` is NOT a heading in this profile and the
  test says so. If the engine one day learns to split a marker from the clause on its line,
  that test should fail and be rewritten, not deleted.
* **The shared tables.** This profile repeats `id_regulation`'s ladder, abbreviations and
  scoring vocabulary because a profile cannot extend another. The drift guard below is what
  makes that duplication safe.
"""

from __future__ import annotations

import pytest

from spec import Profile, Registry, default_registry


@pytest.fixture(scope="module")
def registry() -> Registry:
    return default_registry()


@pytest.fixture(scope="module")
def md(registry: Registry) -> Profile:
    return registry.get("id_regulation_md")


@pytest.fixture(scope="module")
def pdf(registry: Registry) -> Profile:
    return registry.get("id_regulation@1.0")


# ===========================================================================
# Routing · Markdown only, and it must win there
# ===========================================================================
SAMPLE = (
    "## PERATURAN BUPATI TABALONG NOMOR 29 TAHUN 2014\n## DENGAN RAHMAT TUHAN YANG MAHA ESA"
)


def test_a_markdown_regulation_routes_to_the_markdown_profile(registry: Registry) -> None:
    assert registry.route(mime="text/markdown", sample=SAMPLE).ref == "id_regulation_md@1.1"


@pytest.mark.parametrize("mime", ["application/pdf", "text/plain", "text/html"])
def test_other_formats_still_route_to_the_pdf_tuned_profile(
    registry: Registry, mime: str
) -> None:
    assert registry.route(mime=mime, sample=SAMPLE).ref == "id_regulation@1.0"


def test_a_markdown_note_is_not_claimed(registry: Registry) -> None:
    assert registry.route(mime="text/markdown", sample="# Notes\nbuy milk").ref == "generic@1.0"


def test_priority_beats_any_difference_in_content_hits(md: Profile, pdf: Profile) -> None:
    """! `id_regulation` can score MORE content hits than this profile on the same text. The
    priority gap must be larger than the most a hit-count difference can be worth, or a
    Markdown regulation is claimed by the profile tuned for PDFs."""
    most_hits = len(pdf.spec.match.content) * 10
    assert md.spec.match.priority > most_hits


# ===========================================================================
# Structure · what is a heading
# ===========================================================================
@pytest.mark.parametrize(
    ("line", "unit"),
    [
        ("Pasal 5", "pasal"),
        ("Pasal5", "pasal"),  # glued · 3,304 lines in a 1-in-12 sample
        ("Pasal 120 .", "pasal"),
        ("Pasal 145 :", "pasal"),
        ("Pasal 5A", "pasal"),
        ("Pasal I", "pasal"),  # the article of an amending regulation
        ("PASAL 7", "pasal"),
        ("BAB II", "bab"),
        ("BABV", "bab"),  # glued
        ("BABI KETENTUAN UMUM", "bab"),  # glued, title on the line
        ("BAB IX KETENTUAN PENUTUP", "bab"),
        ("BAB VIII KETENTUAN PIDANA", "bab"),
        ("Bagian Kedua", "bagian"),
        ("Bagian Keduabelas", "bagian"),  # one word in this corpus
        ("Bagian Keempat Pembuktian", "bagian"),
        ("BAGIAN KETIGA", "bagian"),
        ("Paragraf 2", "paragraf"),
        ("Paragraf Kedua Penyaluran dan Penyerahan", "paragraf"),
        ("Menimbang", "menimbang"),
        ("Menimbang :", "menimbang"),
        ("Mengingat :", "mengingat"),
        ("MEMUTUSKAN :", "memutuskan"),
        ("MEMUTUSKAN : . . .", "memutuskan"),
        ("Menetapkan", "menetapkan"),
        ("PENJELASAN", "penjelasan"),
        ("PENJELASAN ATAS", "penjelasan"),
        ("I. UMUM", "umum"),
        ("I.UMUM", "umum"),
        ("II. PASAL DEMI PASAL", "pasal_demi_pasal"),
        ("LAMPIRAN", "lampiran"),
        ("LAMPIRAN II", "lampiran"),
        (
            "LAMPIRAN II PERATURAN WALIKOTA PONTIANAK NOMOR 6 TAHUN 2014 TENTANG PETUNJUK",
            "lampiran",
        ),
    ],
)
def test_structural_markers(md: Profile, line: str, unit: str) -> None:
    found = md.structural(line)
    assert found is not None and found.name == unit


@pytest.mark.parametrize(
    "line",
    [
        # An amending regulation's own clauses are prose, not articles
        "Pasal 9 dihapus.",
        "Pasal 12 diubah sehingga berbunyi:",
        "Pasal 1a diubah seluruhnya sehingga berbunyi sebagai berikut:",
        # A cross-reference that opens a sentence
        "Pasal 5 ayat (2) Undang-Undang Dasar Negara Republik Indonesia Tahun 1945;",
        # The explanation's per-article entry, one line · not a heading (see the gap below)
        "Pasal 5 Cukup jelas.",
        # A catch-word at a page break
        "Pasal 97 ...",
        "BAB IX . . .",
        # A word that is also a marker
        "Babi hutan dilindungi",
        "Bagian Hukum",  # an office of a Sekretariat Daerah
        "Bagian Keuangan",
        "Bagian ini memuat sasaran dan langkah-langkah",
        "penjelasan Bupati dalam rapat paripurna",
        "lampiran yang merupakan bagian tidak terpisahkan",
        "Lampiran yang merupakan bagian yang tidak terpisahkan dari Peraturan Pemerintah ini.",
        # A table row is never a heading, whatever its first cell says
        "| Bab I | Pendahuluan | Pendahuluan |",
        "| Pasal 5 | Cukup jelas |",
        # OCR damage that would give a wrong locator rather than none
        "Pasa122",
        "Pasai 84",
    ],
)
def test_prose_and_near_misses_are_never_headings(md: Profile, line: str) -> None:
    assert md.structural(line) is None


@pytest.mark.parametrize(
    "line",
    [
        "Menimbang : a. bahwa untuk melaksanakan ketentuan Pasal 4",
        "Mengingat : 1. Undang-Undang Nomor 12 Tahun 2011",
        "Menetapkan : PERATURAN DAERAH TENTANG RETRIBUSI",
    ],
)
def test_documented_gap_a_marker_and_its_clause_on_one_line(md: Profile, line: str) -> None:
    """! A KNOWN GAP, pinned. 1,141 of 3,794 `Menimbang` lines and 1,826 of 3,817 `Menetapkan`
    lines in the sample look like this. `structural()` accepts a marker plus a SHORT title,
    ✗ a marker plus a clause, and widening it would turn amendment clauses into headings.
    When the engine can split the marker from its clause, this test should fail and be
    rewritten to assert the marker is found."""
    assert md.structural(line) is None


def test_typography_is_not_trusted(md: Profile) -> None:
    """`##` is applied inconsistently by the converter (`## Pasal 2`, then plain `Pasal 4`)."""
    assert not md.option("trust_typography", False)


# ===========================================================================
# Identity · a document is cited by its OWN title
# ===========================================================================
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("PERATURAN BUPATI TABALONG NOMOR 29 TAHUN 2014 TENTANG", "Perbup 29/2014"),
        ("PERATURAN PEMERINTAH REPUBLIK INDONESIA NOMOR 26 TAHUN 2011 TENTANG", "PP 26/2011"),
        ("UNDANG-UNDANG REPUBLIK INDONESIA NOMOR1TAHUN1966 TENTANG", "UU 1/1966"),  # glued
        ("UNDANG-UNDANG REPUBLIK INDONESIA NOMOR8TAHUN1970", "UU 8/1970"),
        ("PERATURANBUPATITABALONG NOMOR 29 TAHUN 2014", "Perbup 29/2014"),  # glued type
        ("KEPUTUSANPRESIDEN REPUBLIK INDONESIA NOMOR 84 TAHUN 2002", "Keppres 84/2002"),
        ("PERATURAN DAERAH KABUPATEN MERANGIN NOMOR   3   TAHUN 2016", "Perda Kab. 3/2016"),
        ("QANUN KABUPATEN SIMEULUE NOMOR 25 TAHUN 2012", "Qanun Kab. 25/2012"),
        ("PERATURAN PEMERINTAH NOMOR 67 TAHUN 20100", "PP 67/2010"),  # a stray digit
        (
            "PERATURAN MENTERI KEUANGAN NOMOR 123/PMK.03/2019 TAHUN 2019",
            "Permen 123/PMK.03/2019",
        ),
    ],
)
def test_citations_survive_what_the_converter_does_to_a_title(
    md: Profile, text: str, expected: str
) -> None:
    assert md.cite(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # A law CITED in Mengingat is Title-case; the document's own title is in capitals
        "Undang-Undang Nomor 28 Tahun 2009 tentang Pajak Daerah dan Retribusi Daerah",
        "Peraturan Pemerintah Nomor 18 Tahun 2016 tentang Perangkat Daerah",
        # An OCR'd digit must give no citation, not a wrong one
        "PERATURAN BUPATI SITUBONDO NOMOR J8 TAHUN 2013",
        "PERATURAN BUPATI SINTANG NOMOR 4S TAHUN 2013",
    ],
)
def test_a_wrong_identifier_is_worse_than_none(md: Profile, text: str) -> None:
    """! The exact-match path trusts `identifier` completely. Over 3,871 files, matching
    case-insensitively cited the first law mentioned in Mengingat for 9% of documents."""
    assert md.cite(text) is None


def test_the_documents_own_title_wins_over_a_law_it_cites(md: Profile) -> None:
    window = (
        "PERATURAN DAERAH KABUPATEN MERANGIN NOMOR 3 TAHUN 2016 TENTANG KETERTIBAN UMUM\n"
        "Menimbang : bahwa ...\n"
        "Mengingat : 3. Undang-Undang Nomor 23 Tahun 2014 tentang Pemerintahan Daerah"
    )
    assert md.cite(window) == "Perda Kab. 3/2016"


def test_cross_references_are_still_case_insensitive(md: Profile) -> None:
    """`flags` is empty for this profile, so `cross_ref` carries its own `(?i:...)`."""
    assert md.cross_ref is not None
    for text in ("Pasal 5 ayat (2)", "PASAL 5 AYAT (2)", "pasal 5 ayat (2)"):
        match = md.cross_ref.search(text)
        assert match and match.group("pasal") == "5" and match.group("ayat") == "2"


# ===========================================================================
# Cleanup · page furniture goes, content stays
# ===========================================================================
@pytest.mark.parametrize(
    "text",
    [
        "Cukup jelas.",
        "<!-- image -->",
        "2 -",  # `- 2 -` after the extractor strips the bullet
        "- 31 -",
        "-17 -",
        "PRESIDEN REPUBLIK INDONESIA",
        "PRESIDEN REPUBLIK INDONESIA,",
        "REPUBLIK INDONESIA",
        "SALINAN",
        "Ttd.",
        ":",
        "~",
        # Catch-words at a page break
        "Dalam . . .",
        "Bilamana ...",
        "1. Tindakan …",
        "Pasal 97 ...",
        "BAB IX . . .",
        "Mengingat : …",
    ],
)
def test_page_furniture_is_boilerplate(md: Profile, text: str) -> None:
    assert md.is_boilerplate(text)


@pytest.mark.parametrize(
    "text",
    [
        "Pasal 5",
        # ! A bare number can be the number of a `Pasal` split from it by the converter
        "12",
        "Dengan Peraturan Pemerintah ini, pusat pemerintahan Kota Padang dipindahkan.",
        "(1) Setiap orang harus menjaga dan memelihara keberadaan fasilitas umum.",
        "Dalam rangka mengurangi risiko akibat kejadian bencana gempa bumi dan tsunami...",
        "Peraturan ini mulai berlaku pada tanggal diundangkan.",
        "PRESIDEN REPUBLIK INDONESIA mengesahkan peraturan ini",
        "a. sebelah utara berbatasan dengan Kabupaten Padang Pariaman;",
    ],
)
def test_content_is_not_boilerplate(md: Profile, text: str) -> None:
    assert not md.is_boilerplate(text)


# ===========================================================================
# The tables shared with `id_regulation` · drift guard
# ===========================================================================
def test_every_ladder_entry_of_the_pdf_profile_is_the_same_here(
    md: Profile, pdf: Profile
) -> None:
    for kind, rank in pdf.spec.identity.authority.items():
        assert md.spec.identity.authority.get(kind) == rank, kind


def test_every_abbreviation_of_the_pdf_profile_is_the_same_here(
    md: Profile, pdf: Profile
) -> None:
    for kind, short in pdf.spec.identity.abbreviations.items():
        assert md.spec.identity.abbreviations.get(kind) == short, kind


def test_every_type_of_the_pdf_profile_is_still_listed_here(md: Profile, pdf: Profile) -> None:
    assert set(pdf.spec.identity.types) <= set(md.spec.identity.types)


def test_the_scoring_block_is_identical(md: Profile, pdf: Profile) -> None:
    """The block, ✗ `scoring_vocabulary`: that one folds in the ladder, which legitimately
    carries this profile's added types."""
    assert md.spec.scoring == pdf.spec.scoring


def test_what_this_profile_adds_is_exactly_the_documented_set(
    md: Profile, pdf: Profile
) -> None:
    added = set(md.spec.identity.types) - set(pdf.spec.identity.types)
    assert added == {
        "QANUN ACEH",
        "QANUN KABUPATEN",
        "QANUN KOTA",
        "PERATURANDAERAHKABUPATEN",
        "PERATURANDAERAHPROVINSI",
        "PERATURANDAERAHKOTA",
        "PERATURANPEMERINTAH",
        "PERATURANPRESIDEN",
        "PERATURANMENTERI",
        "PERATURANGUBERNUR",
        "PERATURANWALIKOTA",
        "PERATURANBUPATI",
        "KEPUTUSANPRESIDEN",
    }


def test_every_added_type_has_an_abbreviation_and_a_rank(md: Profile, pdf: Profile) -> None:
    for kind in set(md.spec.identity.types) - set(pdf.spec.identity.types):
        assert kind in md.spec.identity.abbreviations, kind
        assert kind in md.spec.identity.authority, kind
