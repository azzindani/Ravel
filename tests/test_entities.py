"""Legal reference extraction — the graph's raw material, driven by the profile.

The comparison case throughout is `kg_core.py`, whose regulation pattern requires `No.`
where Indonesian legal text writes `Nomor`. These tests pin the spelled-out forms it
misses, because those are the corpus: regional instruments are roughly 60% of it and none
of them appear in that pattern's seven-way alternation.
"""

from __future__ import annotations

import pytest

from enrich import ReferenceKind, extract_references, reference_counts
from spec import Registry


@pytest.fixture(scope="module")
def profile():
    return Registry.load().get("id_regulation")


@pytest.mark.parametrize(
    ("body", "identifier"),
    [
        ("berdasarkan Undang-Undang Nomor 40 Tahun 2007 tentang PT", "UU 40/2007"),
        ("mengacu pada Peraturan Pemerintah Nomor 24 Tahun 2018", "PP 24/2018"),
        ("dalam Peraturan Daerah Kabupaten Boyolali Nomor 16 Tahun 2022", "Perda Kab. 16/2022"),
        ("diubah dengan Peraturan Bupati Nomor 5 Tahun 2020", "Perbup 5/2020"),
        ("sesuai Peraturan Gubernur Nomor 7 Tahun 2020", "Pergub 7/2020"),
    ],
)
def test_spelled_out_citations_are_found(profile, body: str, identifier: str) -> None:
    """! Every one of these is a form `kg_core.py` misses. `No\\.?` does not match
    `Nomor`: it consumes the `No`, then needs a digit and meets `mor`."""
    refs = extract_references(profile, body)

    found = [r.identifier for r in refs if r.kind is ReferenceKind.REGULATION]
    assert found == [identifier]


def test_references_carry_the_span_they_were_read_from(profile) -> None:
    """! A mention with a position can be highlighted, re-verified against the source and
    mapped back to a span. A mention without one is an assertion."""
    body = "sebagaimana diatur dalam Undang-Undang Nomor 40 Tahun 2007 tentang PT"
    refs = extract_references(profile, body)
    (ref,) = [r for r in refs if r.kind is ReferenceKind.REGULATION]

    assert body[ref.start : ref.end] == ref.text


def test_internal_pointers_are_a_different_kind(profile) -> None:
    """A `Pasal 5 ayat (2)` reference is an edge inside the document, not between two."""
    refs = extract_references(profile, "sebagaimana dimaksud dalam Pasal 5 ayat (2) huruf a")

    assert [r.kind for r in refs] == [ReferenceKind.INTERNAL]
    assert refs[0].fields == {"pasal": "5", "ayat": "2", "huruf": "a"}


def test_overlapping_matches_are_counted_once(profile) -> None:
    """! Regulation and internal patterns overlap. Counting a reference twice inflates
    every degree metric computed from it downstream."""
    refs = extract_references(profile, "Pasal 5 Undang-Undang Nomor 40 Tahun 2007")

    spans = [r.span for r in refs]
    assert len(spans) == len(set(spans))
    for i, a in enumerate(refs):
        for b in refs[i + 1 :]:
            assert not (a.start <= b.start and b.end <= a.end), "one contains the other"


def test_references_come_back_in_document_order(profile) -> None:
    body = (
        "Peraturan Pemerintah Nomor 24 Tahun 2018 mencabut "
        "Peraturan Pemerintah Nomor 9 Tahun 2016"
    )
    refs = extract_references(profile, body)

    assert [r.start for r in refs] == sorted(r.start for r in refs)
    assert [r.identifier for r in refs] == ["PP 24/2018", "PP 9/2016"]


def test_an_unknown_type_yields_no_identifier_rather_than_a_guess(profile) -> None:
    """! The exact-match bypass trusts `identifier` completely, so a wrong one is worse
    than an absent one."""
    refs = extract_references(profile, "sebagaimana dimaksud dalam Pasal 5 ayat (2)")

    assert all(r.identifier is None for r in refs)


def test_prose_without_citations_produces_nothing(profile) -> None:
    prose = "Ketentuan ini mulai berlaku pada tanggal diundangkan."
    assert extract_references(profile, prose) == []


def test_counts_report_distinct_regulations_not_mentions(profile) -> None:
    """The incumbent's `kg_cross_ref_count` counts mentions. Distinct targets is the
    number an edge list actually needs — a clause citing one law four times is one edge."""
    body = (
        "Undang-Undang Nomor 40 Tahun 2007 mengatur hal ini, dan "
        "Undang-Undang Nomor 40 Tahun 2007 juga menyebut Pasal 5 ayat (1)"
    )
    counts = reference_counts(extract_references(profile, body))

    assert counts["regulation"] == 2
    assert counts["distinct_regulations"] == 1
    assert counts["internal"] == 1


def test_counts_of_nothing_are_zero_not_missing(profile) -> None:
    counts = reference_counts([])

    assert counts == {
        "regulation": 0,
        "internal": 0,
        "total": 0,
        "distinct_regulations": 0,
    }


def test_a_profile_without_citation_patterns_extracts_nothing(profile) -> None:
    """! A profile that cannot describe citations must produce none, never fall back to a
    built-in pattern. A built-in fallback is how corpus knowledge gets back into code."""
    generic = Registry.load().get("generic")

    assert extract_references(generic, "Undang-Undang Nomor 40 Tahun 2007") == []
