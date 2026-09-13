"""Document profiles: the registry, the engine, and the data it loads.

These tests defend two different things. The **engine** tests assert behaviour that
must hold for any profile. The **data** tests assert that the shipped Indonesian
profile encodes the knowledge whose absence produced ID_Legal's defects
(`ABSORPTION.md` §4) — those are regression tests against a corpus, not against code.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from spec import Profile, ProfileSpec, Registry, RegistryError, default_registry


@pytest.fixture(scope="module")
def registry() -> Registry:
    return default_registry()


@pytest.fixture(scope="module")
def idreg(registry: Registry) -> Profile:
    return registry.get("id_regulation")


@pytest.fixture(scope="module")
def generic(registry: Registry) -> Profile:
    return registry.get("generic")


# ===========================================================================
# The engine — true for any profile
# ===========================================================================
def test_registry_loads_the_shipped_profiles(registry: Registry) -> None:
    assert set(registry.refs()) >= {"generic@1.0", "id_regulation@1.0"}


def test_bare_id_resolves_to_a_version(registry: Registry) -> None:
    assert registry.get("id_regulation").ref == "id_regulation@1.0"


def test_unknown_profile_names_what_exists(registry: Registry) -> None:
    with pytest.raises(KeyError, match="id_regulation@1.0"):
        registry.get("klingon_statutes")


def test_config_hash_changes_when_a_pattern_changes(idreg: Profile) -> None:
    """! This is what makes a profile safe to iterate on: an edited pattern
    invalidates the artifacts it produced instead of silently reusing them
    (LOOPHOLES.md §1)."""
    before = idreg.config_hash
    edited = idreg.spec.model_copy(deep=True)
    edited.structure.units[0].pattern = "^CHANGED"
    assert edited.config_hash != before


def test_config_hash_is_stable_across_loads(registry: Registry) -> None:
    reloaded = Registry.load()
    fresh = reloaded.get("id_regulation").config_hash
    assert fresh == registry.get("id_regulation").config_hash


def test_filename_must_agree_with_contents(tmp_path: Path) -> None:
    """A file named @1.0 whose body says 1.1 is ambiguous exactly where a cache key
    cannot tolerate ambiguity."""
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "thing@1.0.yaml").write_text(
        'id: thing\nversion: "1.1"\ntitle: Mismatched\n', encoding="utf-8"
    )
    with pytest.raises(RegistryError, match="must be thing@1.1.yaml"):
        Registry.load(tmp_path)


def test_duplicate_unit_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate structural unit"):
        ProfileSpec.model_validate(
            {
                "id": "x",
                "version": "1.0",
                "title": "X",
                "structure": {
                    "units": [
                        {"name": "a", "level": 1, "pattern": "^A"},
                        {"name": "a", "level": 2, "pattern": "^B"},
                    ]
                },
            }
        )


def test_unknown_regex_flag_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown regex flags"):
        ProfileSpec.model_validate(
            {"id": "x", "version": "1.0", "title": "X", "identity": {"flags": ["sideways"]}}
        )


# -- routing ----------------------------------------------------------------
@pytest.mark.parametrize(
    ("mime", "sample", "expected"),
    [
        ("application/pdf", "PERATURAN BUPATI BOYOLALI ... MEMUTUSKAN:", "id_regulation@1.0"),
        ("text/plain", "Undang-Undang Nomor 28 Tahun 2007", "id_regulation@1.0"),
        ("application/pdf", "Annual Report 2024\nSection 1 Overview", "generic@1.0"),
        ("text/html", "<h1>Annual Report</h1> Section 1", "generic@1.0"),
        ("text/markdown", "# Notes", "generic@1.0"),
        ("image/png", "whatever", None),
    ],
)
def test_routing_picks_the_specific_profile_over_the_fallback(
    registry: Registry, mime: str, sample: str, expected: str | None
) -> None:
    """! The data is not only PDFs. HTML, markdown and plain text route through the
    same engine — a profile describes a KIND OF DOCUMENT, not a file format."""
    chosen = registry.route(mime=mime, sample=sample)
    assert (chosen.ref if chosen else None) == expected


def test_negative_priority_does_not_disqualify_the_fallback(generic: Profile) -> None:
    """The generic profile carries priority -100 so anything specific outranks it.
    That must not be confused with 'does not apply'."""
    score = generic.score(mime="text/html", sample="anything at all")
    assert score is not None and score < 0
    assert generic.score(mime="image/png", sample="x") is None


def test_routing_is_deterministic(registry: Registry) -> None:
    """Two runs over one corpus must assign the same profile, or the cache lies."""
    args = {"mime": "application/pdf", "sample": "Section 1 Overview"}
    assert {registry.route(**args).ref for _ in range(5)} == {registry.route(**args).ref}


# ===========================================================================
# The Indonesian profile — regression tests against a real corpus
# ===========================================================================
@pytest.mark.parametrize(
    ("line", "unit", "level"),
    [
        ("BAB II", "bab", 3),
        ("BAB XIV", "bab", 3),
        ("BUKU KESATU", "buku", 2),
        ("BAGIAN KEDUA", "bagian", 4),
        ("Bagian Ketiga Ketentuan Umum", "bagian", 4),
        ("PARAGRAF 2", "paragraf", 5),
        ("Pasal 9", "pasal", 6),
        ("PASAL 12A", "pasal", 6),
        ("MEMUTUSKAN:", "memutuskan", 3),
        ("BAB II KETENTUAN UMUM", "bab", 3),
    ],
)
def test_structural_markers(idreg: Profile, line: str, unit: str, level: int) -> None:
    found = idreg.structural(line)
    assert found is not None, line
    assert (found.name, found.level) == (unit, level)


@pytest.mark.parametrize(
    "line",
    [
        "sebagaimana dimaksud dalam Pasal 9 ayat (3)",
        "Wajib Pajak wajib menyelenggarakan pembukuan.",
        "Pasal 9 dihapus.",
        "Pasal 12 diubah sehingga berbunyi:",
        "Pasal 9 mengatur kewajiban pembukuan",
        "Bab II diubah sehingga berbunyi:",
        "",
        "   ",
    ],
)
def test_prose_is_never_a_heading(idreg: Profile, line: str) -> None:
    """! Amending regulations are full of lines that OPEN with a marker but are
    prose. Counting them as headings splits a document at every amendment clause,
    and corrupts any measurement built on the same detector."""
    assert idreg.structural(line) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("PERATURAN DAERAH KABUPATEN BOYOLALI NOMOR 16", "PERATURAN DAERAH KABUPATEN"),
        ("PERATURAN BUPATI BOYOLALI", "PERATURAN BUPATI"),
        ("UNDANG-UNDANG REPUBLIK INDONESIA", "UNDANG-UNDANG"),
        ("PERATURAN MENTERI KEUANGAN", "PERATURAN MENTERI"),
        ("Surat Edaran Direktur Jenderal", None),
    ],
)
def test_document_type_is_general_not_one_ministry(
    idreg: Profile, text: str, expected: str | None
) -> None:
    """The incumbent corpus has 10.66% `Unknown` type because its parser matched only
    PERATURAN MENTERI KEUANGAN. These are the types it could not see."""
    assert idreg.document_type(text) == expected


def test_longest_type_wins(idreg: Profile) -> None:
    found = idreg.document_type("PERATURAN DAERAH KABUPATEN SLEMAN")
    assert found == "PERATURAN DAERAH KABUPATEN"


def test_title_recovers_the_enacting_body(idreg: Profile) -> None:
    """8.62% of the incumbent corpus has `Unknown` enacting_body. It was always
    there, between the type and NOMOR."""
    m = idreg.title.search("PERATURAN BUPATI BOYOLALI NOMOR 41 TAHUN 2011 TENTANG RETRIBUSI")
    assert m is not None
    assert m.group("type").upper() == "PERATURAN BUPATI"
    assert m.group("body").strip().upper() == "BOYOLALI"
    assert (m.group("number"), m.group("year")) == ("41", "2011")


def test_cross_reference_captures_the_full_locator(idreg: Profile) -> None:
    m = idreg.cross_ref.search("sebagaimana dimaksud dalam Pasal 5 ayat (2) huruf a")
    assert m is not None
    assert (m.group("pasal"), m.group("ayat"), m.group("huruf")) == ("5", "2", "a")


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("(2) Dalam hal Wajib Pajak", ("ayat", "2", "(2)")),
        ("a. orang pribadi;", ("huruf", "a", "a")),
        ("3. Ketentuan lebih lanjut", ("angka", "3", "3")),
    ],
)
def test_subunits(idreg: Profile, line: str, expected: tuple[str, str, str]) -> None:
    """! The marker keeps the form the document writes, because a citation does:
    `Pasal 9 ayat (2)`, not `Pasal 9 ayat 2`. The bare value is kept alongside it for
    sorting and cross-reference matching."""
    assert idreg.subunit(line) == expected


@pytest.mark.parametrize("text", ["Cukup jelas.", "Cukup jelas", "  cukup  jelas  "])
def test_boilerplate(idreg: Profile, text: str) -> None:
    """30,520 rows of the incumbent corpus are exactly this string."""
    assert idreg.is_boilerplate(text)


def test_real_content_is_not_boilerplate(idreg: Profile) -> None:
    assert not idreg.is_boilerplate("Cukup jelas sebagaimana diatur dalam Pasal 4.")


# ===========================================================================
# The generic profile — same engine, different data, no Indonesian anywhere
# ===========================================================================
@pytest.mark.parametrize(
    ("line", "unit"),
    [
        ("CHAPTER IV", "chapter"),
        ("3.2 Scope of Work", "numbered_2"),
        ("Section 12", "section"),
        ("APPENDIX A", "appendix"),
        ("LAMPIRAN I", "appendix"),
        ("REFERENCES", "references"),
    ],
)
def test_generic_profile_reads_ordinary_documents(
    generic: Profile, line: str, unit: str
) -> None:
    found = generic.structural(line)
    assert found is not None, line
    assert found.name == unit


def test_generic_profile_still_refuses_prose(generic: Profile) -> None:
    assert generic.structural("This section explains the scope of the work.") is None


def test_generic_profile_trusts_typography(generic: Profile) -> None:
    """With no corpus patterns to lean on, font size is the only signal there is —
    so unlike id_regulation, the generic profile keeps the extractor's guesses."""
    assert generic.option("trust_typography") is True


def test_the_structure_hash_ignores_what_does_not_change_a_document(idreg: Profile) -> None:
    """! Extraction keys on `structure_hash`, and the split earns its keep the first
    time a profile is corrected. Fixing the citation pattern recovered 37 uncited
    documents and corrected 21 that cited the regulation they implement — and changes
    nothing about how a document is split into blocks. Under a whole-spec key it would
    have discarded 931 extracted documents to reproduce them byte for byte."""
    spec = idreg.spec
    retitled = spec.model_copy(deep=True)
    retitled.identity.citation = r"(?P<type>{types})\s+NOMOR\s+(?P<number>\d+)"
    retitled.cleanup.boilerplate = []

    assert retitled.config_hash != spec.config_hash
    assert retitled.structure_hash == spec.structure_hash


def test_the_structure_hash_still_tracks_structure(idreg: Profile) -> None:
    spec = idreg.spec
    changed = spec.model_copy(deep=True)
    changed.structure.units[-1].marker_only = False

    assert changed.structure_hash != spec.structure_hash


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("PERATURAN BUPATI INDRAMAYU\nNOMOR : 80 TAHUN 2021", "Perbup 80/2021"),
        ("PERATURAN MENTERI KEUANGAN\nNOMOR109 TAHUN 2023", "Permen 109/2023"),
        ("PERATURAN BUPATI CIAMIS\nNOMOR :   44  TAHUN  2012", "Perbup 44/2012"),
        # The number is blank in the source. None, not a guess.
        ("PERATURAN BUPATI TEGAL\nNOMOR\nTAHUN 2023", None),
        # A cross-reference in prose is not the document's own citation.
        ("Menimbang bahwa Pasal 9 ayat (3) berlaku.", None),
    ],
)
def test_citations_survive_ocr_spacing(idreg: Profile, text: str, expected: str | None) -> None:
    """OCR spacing is not a property of the document, so the separator after NOMOR is
    loose. The body segment is not — widening it lets the pattern step over a
    document's own NOMOR to reach a later one."""
    assert idreg.cite(text) == expected


def test_a_document_is_cited_by_its_own_title_not_by_what_it_implements(
    idreg: Profile,
) -> None:
    """! `25_juklak_perda_17_2011` reads `PERATURAN BUPATI CIAMIS NOMOR : 25 TAHUN 2012`
    in its title and `PERATURAN DAERAH KABUPATEN CIAMIS NOMOR 17 TAHUN 2011` two lines
    later. A stricter separator skipped the first and cited the second — the wrong
    regulation, in 21 documents of 931."""
    text = (
        "BUPATI CIAMIS\nPERATURAN BUPATI CIAMIS\nNOMOR\n:   25   TAHUN 2012\nTENTANG\n"
        "PETUNJUK PELAKSANAAN PERATURAN DAERAH KABUPATEN CIAMIS\n"
        "NOMOR 17 TAHUN  2011 TENTANG RETRIBUSI"
    )
    assert idreg.cite(text) == "Perbup 25/2012"


def test_a_marker_that_is_also_an_ordinary_word_can_be_case_sensitive(
    idreg: Profile,
) -> None:
    """! `penjelasan` heads the explanatory annex of a regulation and is also an
    ordinary Indonesian word. Matched case-insensitively, `Penjelasan realisasi ini
    karena ...` became a heading: 2,310 chunks located themselves at `Penjelasan` and
    1,112 more at that sentence. An ambiguous locator is a citation no reader can
    follow."""
    assert idreg.structural("PENJELASAN ATAS PERATURAN DAERAH") is not None
    assert idreg.structural("PENJELASAN UMUM") is not None
    assert idreg.structural("Penjelasan realisasi ini karena disesuaikan") is None


def test_case_insensitivity_remains_the_default(idreg: Profile) -> None:
    """Only markers that collide with ordinary words need the stricter rule."""
    assert idreg.structural("bab i") is not None
    assert idreg.structural("BAB I") is not None


# -- where the registry is found ------------------------------------------------------


def test_a_checkout_registry_is_found_from_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """! Found from a CI job, and only once the CLI ran from an installed wheel.

    The packaged fallback resolves `parents[2]`, which is the repository root when `src/`
    is on the path and `<venv>/Lib/` when Ravel is installed — so `ravel profiles list`
    refused with "no profile registry at <venv>/Lib/registry/profiles". The registry is
    data in git reviewed alongside the code, so the copy an operator wants is almost
    always the one in the checkout they are standing in.
    """
    from spec import registry_root

    (tmp_path / "registry" / "profiles").mkdir(parents=True)
    monkeypatch.delenv("RAVEL_REGISTRY", raising=False)
    monkeypatch.chdir(tmp_path)

    assert registry_root() == tmp_path / "registry"


def test_an_explicit_registry_always_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Argument, then RAVEL_REGISTRY, then the checkout. A profile decides how documents
    are parsed, so an explicit choice must never lose to something that was merely
    nearby."""
    from spec import registry_root

    (tmp_path / "registry" / "profiles").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    monkeypatch.setenv("RAVEL_REGISTRY", str(tmp_path / "from-env"))
    assert registry_root() == tmp_path / "from-env"
    assert registry_root(tmp_path / "explicit") == tmp_path / "explicit"


def test_nothing_searches_upward(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """! A registry found two directories above the one you are standing in is a surprise,
    and the surprise is that documents parse differently than you expected."""
    from spec import registry_root

    (tmp_path / "registry" / "profiles").mkdir(parents=True)
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    monkeypatch.delenv("RAVEL_REGISTRY", raising=False)
    monkeypatch.chdir(nested)

    assert registry_root() != tmp_path / "registry"
