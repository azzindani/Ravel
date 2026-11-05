"""Relations between regulations: what a sentence says about another instrument.

Every sentence here is the shape of one read in `Azzindani/ID_REG_Markdown`; the near-misses
are the ones a keyword search gets wrong, which is why the cue is bound to its nearest
citation and not to the document as a whole.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from enrich.relations import (
    DocIdentity,
    Index,
    RelationExtractor,
    derive_status,
    identity_of,
    resolve,
)
from spec import Profile, RelationRule, default_registry


@dataclass
class FakeChunk:
    """The fields the extractor reads from a chunk, nothing else."""

    body: str
    seq: int = 0
    id: str = "c0"
    doc_id: str = "d0"
    chapter: str = "BAB I - KETENTUAN UMUM"
    article: str = "Pasal 1"
    regulation_type: str | None = None
    enacting_body: str | None = None
    regulation_number: str | None = None
    year: int | None = None


@pytest.fixture(scope="module")
def profile() -> Profile:
    return default_registry().get("id_regulation_md")


@pytest.fixture(scope="module")
def extractor(profile: Profile) -> RelationExtractor:
    return RelationExtractor(profile)


def edges(
    extractor: RelationExtractor, *bodies: str, own: DocIdentity | None = None, **kw: str
):
    chunks = [FakeChunk(body=b, seq=i, id=f"c{i}", **kw) for i, b in enumerate(bodies)]
    return {
        (r.relation, r.target_number, r.target_year): r for r in extractor.extract(chunks, own)
    }


def own_identity(
    number: str, year: int, kind: str = "PERATURAN BUPATI", body: str = ""
) -> DocIdentity:
    return DocIdentity("self", kind, body, number, year, "PERBUP")


# ---------------------------------------------------------------------------
# a verb binds to the citation beside it
# ---------------------------------------------------------------------------
def test_a_passive_revocation_names_its_target_and_its_region(
    extractor: RelationExtractor,
) -> None:
    found = edges(
        extractor,
        "Pada saat Peraturan Bupati ini mulai berlaku, Peraturan Bupati Lahat Nomor 15 "
        "Tahun 2017 tentang Pelimpahan Sebagian Kewenangan, dicabut dan dinyatakan "
        "tidak berlaku.",
    )
    rel = found[("revokes", "15", 2017)]
    assert rel.target_body == "lahat"
    assert rel.evidence_chunk_id == "c0"
    assert "dicabut dan dinyatakan tidak berlaku" in rel.quote


def test_an_active_revocation_binds_the_citation_after_the_verb(
    extractor: RelationExtractor,
) -> None:
    found = edges(extractor, "Peraturan ini mencabut Peraturan Daerah Nomor 7 Tahun 2009.")
    assert ("revokes", "7", 2009) in found


def test_history_is_a_reference_not_a_revocation(extractor: RelationExtractor) -> None:
    """`X sebagaimana telah diubah dengan Y, dicabut`: X is revoked, Y is how X got here."""
    found = edges(
        extractor,
        "Peraturan Daerah Nomor 5 Tahun 2010 sebagaimana telah diubah dengan Peraturan Daerah "
        "Nomor 3 Tahun 2012, dicabut dan dinyatakan tidak berlaku.",
    )
    assert ("revokes", "5", 2010) in found
    assert ("references", "3", 2012) in found
    assert ("revokes", "3", 2012) not in found


def test_a_title_that_amends_gives_one_edge_and_never_a_self_loop(
    extractor: RelationExtractor,
) -> None:
    found = edges(
        extractor,
        "PERATURAN DAERAH NOMOR 3 TAHUN 2015 TENTANG PERUBAHAN ATAS PERATURAN DAERAH NOMOR 5 "
        "TAHUN 2010 TENTANG PAJAK DAERAH",
        own=own_identity("3", 2015, "PERATURAN DAERAH"),
    )
    assert list(found) == [("amends", "5", 2010)]


def test_a_cue_in_a_cited_title_belongs_to_the_cited_instrument(
    extractor: RelationExtractor,
) -> None:
    """`Perpres 6/2000 tentang Pencabutan Inpres 14/1967`: Perpres 6 did the revoking."""
    found = edges(
        extractor,
        "Peraturan Presiden Nomor 6 Tahun 2000 tentang Pencabutan Instruksi Presiden Nomor 14 "
        "Tahun 1967 tentang Agama, Kepercayaan, dan Adat Istiadat Cina;",
    )
    assert {k[0] for k in found} == {"references"}


def test_a_regulation_that_implements_another_does_not_revoke_it(
    extractor: RelationExtractor,
) -> None:
    found = edges(
        extractor,
        "Semua peraturan pelaksanaan dari Peraturan Presiden Nomor 24 Tahun 2010 dinyatakan "
        "tidak berlaku.",
    )
    assert ("revokes", "24", 2010) not in found


def test_what_would_end_a_regulation_has_not_ended_it(extractor: RelationExtractor) -> None:
    found = edges(
        extractor,
        "Peraturan Presiden Nomor 12 Tahun 2013 tetap berlaku sepanjang belum diganti dengan "
        "peraturan yang baru.",
    )
    assert {k[0] for k in found} == {"references"}


def test_the_items_under_a_revoking_header_are_each_revoked(
    extractor: RelationExtractor,
) -> None:
    header = (
        "Pada saat Peraturan Bupati ini mulai berlaku, peraturan berikut dicabut dan "
        "dinyatakan tidak berlaku:"
    )
    found = edges(
        extractor,
        header
        + "\n\na. Peraturan Bupati Nomor 1 Tahun 2010;"
        + "\n\nb. Peraturan Bupati Nomor 2 Tahun 2011.",
    )
    assert ("revokes", "1", 2010) in found
    assert ("revokes", "2", 2011) in found


def test_a_revocation_of_one_article_is_partial(extractor: RelationExtractor) -> None:
    found = edges(
        extractor,
        "Pasal 114 ayat (6) Keputusan Presiden Nomor 103 Tahun 2001 dicabut dan dinyatakan "
        "tidak berlaku.",
    )
    assert found[("revokes", "103", 2001)].partial


def test_an_explanation_is_not_read(extractor: RelationExtractor) -> None:
    assert not edges(
        extractor,
        "Peraturan Daerah Nomor 7 Tahun 2009 dicabut dan dinyatakan tidak berlaku.",
        chapter="PENJELASAN",
    )


def test_a_repeated_citation_is_one_edge_with_a_count(extractor: RelationExtractor) -> None:
    found = edges(
        extractor,
        "Undang-Undang Nomor 23 Tahun 2014 mengatur hal ini.",
        "Sesuai Undang-Undang Nomor 23 Tahun 2014 pula.",
    )
    assert found[("references", "23", 2014)].mentions == 2


# ---------------------------------------------------------------------------
# the preamble: considerations and legal basis are not acts
# ---------------------------------------------------------------------------
PREAMBLE = (
    "PERATURAN BUPATI LAHAT NOMOR 9 TAHUN 2017 TENTANG PERUBAHAN ATAS PERATURAN BUPATI LAHAT "
    "NOMOR 4 TAHUN 2016 TENTANG PAJAK\n\n"
    ": a. bahwa Undang-Undang Nomor 43 Tahun 1999 tentang Perubahan Atas Undang-Undang Nomor 8 "
    "Tahun 1974 perlu dilaksanakan;\n\n"
    "1. Undang-Undang Nomor 23 Tahun 2014 tentang Pemerintahan Daerah;\n\n"
    "2. Undang-Undang Nomor 28 Tahun 2009 tentang Pajak Daerah dan Retribusi Daerah, "
    "sebagaimana telah diubah dengan Undang-Undang Nomor 1 Tahun 2022;"
)


def test_a_verb_inside_a_considered_title_is_not_this_documents(
    extractor: RelationExtractor,
) -> None:
    found = edges(
        extractor,
        PREAMBLE,
        article="Pembukaan",
        chapter="N/A",
        own=own_identity("9", 2017, body="lahat"),
    )
    assert ("amends", "4", 2016) in found  # the title is this document's own act
    assert not [k for k in found if k[0] == "amends" and k[1] == "8"]
    assert ("references", "43", 1999) in found


def test_the_legal_basis_is_based_on_and_its_history_is_not(
    extractor: RelationExtractor,
) -> None:
    found = edges(extractor, PREAMBLE, article="Pembukaan", chapter="N/A")
    assert ("based_on", "23", 2014) in found
    assert ("based_on", "28", 2009) in found
    assert ("references", "1", 2022) in found  # `sebagaimana telah diubah dengan`


def test_a_preamble_that_is_really_the_whole_document_gets_no_zones(
    extractor: RelationExtractor,
) -> None:
    """Structure not found: the operative articles sit under the opening label, and a zone
    that opened at `bahwa` would swallow the revocation at the end."""
    filler = "Ketentuan umum yang panjang. " * 400
    found = edges(
        extractor,
        ": a. bahwa perlu diatur;\n\n"
        + filler
        + "\n\nPeraturan Bupati Nomor 8 Tahun 2008 dicabut dan dinyatakan tidak berlaku.",
        article="Pembukaan",
        chapter="N/A",
    )
    assert ("revokes", "8", 2008) in found


# ---------------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------------
def doc(
    doc_id: str, kind: str, body: str, number: str, year: int, abbr: str = ""
) -> DocIdentity:
    return DocIdentity(doc_id, kind, body, number, year, abbr)


def rel_of(extractor: RelationExtractor, text: str):
    (found,) = extractor.extract([FakeChunk(body=text)])  # type: ignore[arg-type]
    return found


def test_a_national_instrument_resolves_with_or_without_the_state_named(
    extractor: RelationExtractor,
) -> None:
    index = Index([doc("uu", "UNDANG-UNDANG", "", "23", 2014, "UU")])
    for text in (
        "Dengan Undang-Undang Nomor 23 Tahun 2014 ini.",
        "Dengan Undang-Undang Republik Indonesia Nomor 23 Tahun 2014 ini.",
        "Dengan UU No. 23 Tahun 2014 ini.",
    ):
        assert resolve(rel_of(extractor, text), None, index)[:2] == ("uu", "resolved")


def test_a_citation_naming_a_region_matches_only_that_region(
    extractor: RelationExtractor,
) -> None:
    index = Index(
        [
            doc("a", "PERATURAN BUPATI", "lahat", "15", 2017, "PERBUP"),
            doc("b", "PERATURAN BUPATI", "tapin", "15", 2017, "PERBUP"),
        ]
    )
    rel = rel_of(extractor, "Peraturan Bupati Lahat Nomor 15 Tahun 2017 dicabut.")
    assert resolve(rel, None, index)[:2] == ("a", "resolved")
    other = rel_of(extractor, "Peraturan Bupati Banyuasin Nomor 15 Tahun 2017 dicabut.")
    assert resolve(other, None, index)[:2] == (None, "unresolved")


def test_a_citation_with_no_region_takes_the_citing_documents(
    extractor: RelationExtractor,
) -> None:
    index = Index(
        [
            doc("a", "PERATURAN BUPATI", "lahat", "15", 2017, "PERBUP"),
            doc("b", "PERATURAN BUPATI", "tapin", "15", 2017, "PERBUP"),
        ]
    )
    rel = rel_of(extractor, "Peraturan Bupati Nomor 15 Tahun 2017 dicabut.")
    citing = doc("c", "PERATURAN BUPATI", "tapin", "3", 2018)
    assert resolve(rel, citing, index) == ("b", "inferred", 0.7)
    assert resolve(rel, None, index)[:2] == (None, "ambiguous")


def test_a_cited_type_may_be_shorter_than_the_documents(extractor: RelationExtractor) -> None:
    index = Index([doc("p", "PERATURAN DAERAH KABUPATEN", "lahat", "5", 2010, "PERDA KAB.")])
    for text in (
        "Peraturan Daerah Lahat Nomor 5 Tahun 2010 dicabut.",
        "Perda Lahat Nomor 5 Tahun 2010 dicabut.",
    ):
        assert resolve(rel_of(extractor, text), None, index)[:2] == ("p", "resolved")


def test_a_leading_zero_is_not_a_different_number(extractor: RelationExtractor) -> None:
    index = Index([doc("a", "PERATURAN DAERAH", "", "5", 2010, "PERDA")])
    rel = rel_of(extractor, "Peraturan Daerah Nomor 05 Tahun 2010 dicabut.")
    assert resolve(rel, None, index)[:2] == ("a", "resolved")


def test_identity_is_none_when_the_title_block_named_no_number(profile: Profile) -> None:
    assert identity_of(profile, [FakeChunk(body="x")]) is None  # type: ignore[list-item]
    named = FakeChunk(
        body="x",
        regulation_type="PERATURAN BUPATI",
        enacting_body="LAHAT",
        regulation_number="015",
        year=2017,
    )
    got = identity_of(profile, [named])  # type: ignore[list-item]
    assert got is not None and (got.number, got.year, got.enacting_body) == (
        "15",
        2017,
        "lahat",
    )


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
def test_status_comes_from_resolved_edges_and_is_never_in_force() -> None:
    status = derive_status(
        [
            ("new", "revokes", "old", False),
            ("amender", "amends", "base", False),
            ("part", "revokes", "slice", True),
            ("x", "revokes", None, False),  # points at nothing: marks nothing
            ("y", "references", "plain", False),
        ],
        ["old", "base", "slice", "plain", "new"],
    )
    assert status["old"].status == "revoked" and status["old"].revoked_by == ["new"]
    assert status["base"].status == "amended"
    assert status["slice"].status == "partially_revoked"
    assert status["plain"].status == "none_known"
    assert status["new"].status == "none_known"
    assert "in_force" not in {s.status for s in status.values()}


def test_a_replacement_supersedes_like_a_revocation() -> None:
    status = derive_status([("new", "replaces", "old", False)], ["old", "new"])
    assert status["old"].status == "revoked"


def test_a_full_revocation_outranks_a_partial_one_and_an_amendment() -> None:
    status = derive_status(
        [
            ("a", "amends", "t", False),
            ("b", "revokes", "t", True),
            ("c", "revokes", "t", False),
        ],
        ["t"],
    )
    assert status["t"].status == "revoked"


# ---------------------------------------------------------------------------
# the spec
# ---------------------------------------------------------------------------
def test_a_rule_is_a_cue_or_a_region_never_both_or_neither() -> None:
    with pytest.raises(ValidationError):
        RelationRule(relation="revokes")
    with pytest.raises(ValidationError):
        RelationRule(relation="revokes", cue="x", region_start="y")


def test_relations_do_not_invalidate_chunks(profile: Profile) -> None:
    """Rules are read from finished chunks, so correcting one must not change the key a
    million chunks are cached under -- but the sidecar has its own key."""
    changed = profile.spec.model_copy(
        update={"relations": profile.spec.relations.model_copy(update={"window": 999})}
    )
    assert changed.config_hash == profile.spec.config_hash
    assert changed.relations_hash != profile.spec.relations_hash
