"""BM25 and the ingest-time ranking factors.

Both come from Vera's side of the boundary: `sparse.py` ported near-verbatim, the factors
built to feed `SCORING.md`'s `final = relevance * (1 + sum(w_i * factor_i))`. The tests
that matter here are the ones about *not lying* — an unknown factor must not become a
confident zero, and a vocabulary must not claim coverage it does not have.
"""

from __future__ import annotations

import pytest

from bundle import check_vocabulary_covers_corpus
from enrich import Bm25Vectorizer, TokeniserDrift, compute_factors
from spec import Registry

DOCS = [
    "Setiap orang wajib menaati ketentuan dalam peraturan daerah ini",
    "Pemerintah daerah dapat menetapkan sanksi administratif bagi pelanggar",
    "Ketentuan lebih lanjut mengenai tata cara diatur dengan peraturan bupati",
    "Dalam peraturan ini yang dimaksud dengan daerah adalah kabupaten",
]


@pytest.fixture(scope="module")
def profile():
    return Registry.load().get("id_regulation")


@pytest.fixture
def bm25() -> Bm25Vectorizer:
    return Bm25Vectorizer.fit(DOCS, max_features=200)


# -- BM25 ---------------------------------------------------------------------------


def test_vocabulary_indices_are_deterministic_across_runs() -> None:
    """! Terms are kept by document frequency then sorted alphabetically. Without the
    sort, index assignment depends on Counter ordering and a rebuild produces vectors
    that cannot be compared with the previous build's."""
    assert Bm25Vectorizer.fit(DOCS).vocab == Bm25Vectorizer.fit(DOCS).vocab


def test_scoring_is_inner_product_of_weighted_doc_and_present_query(
    bm25: Bm25Vectorizer,
) -> None:
    """Document vectors carry BM25 weights; the query carries presence, so the dot
    product *is* the BM25 score. Cosine would re-normalise the length term BM25 has
    already handled deliberately."""
    assert set(bm25.query("sanksi administratif").values()) == {1.0}
    assert bm25.score("sanksi administratif", DOCS[1]) > bm25.score(
        "sanksi administratif", DOCS[0]
    )


def test_a_query_with_no_known_terms_scores_zero_rather_than_erroring(
    bm25: Bm25Vectorizer,
) -> None:
    assert bm25.score("quantum chromodynamics", DOCS[0]) == 0.0


def test_longer_documents_do_not_win_by_being_long(bm25: Bm25Vectorizer) -> None:
    """BM25's document-length normalisation, which is the reason for using it over
    raw tf-idf."""
    padded = DOCS[1] + " " + " ".join(["lorem ipsum dolor"] * 60)

    assert bm25.score("sanksi administratif", DOCS[1]) > bm25.score(
        "sanksi administratif", padded
    )


def test_sparsevec_literal_is_one_based_for_pgvector(bm25: Bm25Vectorizer) -> None:
    """! pgvector indexes sparsevec from 1. Emitting 0-based shifts every term by one
    and produces a vector that loads without error and means something else."""
    literal = bm25.to_sparsevec({0: 1.5, 3: 2.0})

    assert literal == "{1:1.5,4:2}/" + str(bm25.dim)


def test_empty_vector_still_declares_its_dimension(bm25: Bm25Vectorizer) -> None:
    assert bm25.to_sparsevec({}) == "{}/" + str(bm25.dim)


def test_round_trip_through_disk_preserves_the_space(bm25: Bm25Vectorizer, tmp_path) -> None:
    path = tmp_path / "bm25.json"
    digest = bm25.save(path)
    loaded = Bm25Vectorizer.load(path)

    assert digest == bm25.sha256()
    assert loaded.document(DOCS[0]) == bm25.document(DOCS[0])


def test_tokeniser_drift_is_refused_not_absorbed(bm25: Bm25Vectorizer, tmp_path) -> None:
    """! The vocabulary is only meaningful under the tokeniser that built it. Loading an
    artifact fitted with a different pattern would produce vectors that silently do not
    match the corpus — the same class of failure as the unapplied instruction."""
    path = tmp_path / "bm25.json"
    bm25.save(path)
    drifted = path.read_text(encoding="utf-8").replace(r"\\b\\w\\w+\\b", r"\\S+")
    path.write_text(drifted, encoding="utf-8")

    with pytest.raises(TokeniserDrift, match="would not match"):
        Bm25Vectorizer.load(path)


def test_the_manifest_entry_is_derived_not_asserted(bm25: Bm25Vectorizer) -> None:
    """! The point of the port. `fit_docs` comes from the object that did the fitting, so
    a manifest cannot claim corpus-wide coverage for a vocabulary fitted on one shard —
    which is exactly what `ABSORPTION.md` §7 turned out to be."""
    spec = bm25.to_spec()

    assert spec.fit_docs == len(DOCS)
    assert spec.vocab_sha256 == bm25.sha256()


def test_a_shard_fitted_vocabulary_fails_the_bundle_check(bm25: Bm25Vectorizer) -> None:
    from bundle import BundleManifest
    from embed import spec_for

    manifest = BundleManifest(
        corpus_id="id_legal",
        run_id="r1",
        chunk_count=748_558,
        source_manifest_sha256="a" * 64,
        dense=spec_for(dim=1024, id="q"),
        sparse=bm25.to_spec(),  # fitted over 4 documents
    )

    ok, detail = check_vocabulary_covers_corpus(manifest)

    assert not ok
    assert "748,558" in detail


# -- factors: the rule is "unknown is not zero" --------------------------------------


def test_authority_comes_from_the_profile_ladder(profile) -> None:
    """`UU 8 · PP 7 · ... · PERBUP 2`, normalised by 10. Corpus knowledge as data."""
    uu = compute_factors(profile=profile, body="x" * 500, document_type="UNDANG-UNDANG")
    perbup = compute_factors(profile=profile, body="x" * 500, document_type="PERATURAN BUPATI")

    assert uu.authority == 0.8
    assert perbup.authority == 0.2


def test_an_unranked_type_is_none_not_zero(profile) -> None:
    """! The load-bearing rule. "The profile does not know" and "ranked lowest" are
    different claims, and collapsing them silently demotes every unrecognised document."""
    f = compute_factors(profile=profile, body="x" * 500, document_type="SURAT EDARAN")

    assert f.authority is None
    assert "authority" not in f.to_dict()


def test_a_missing_year_is_none_not_ancient(profile) -> None:
    assert compute_factors(profile=profile, body="x" * 500, year=None).temporal is None


def test_an_impossible_year_is_none_not_a_low_score(profile) -> None:
    """The incumbent has 569 rows with a year outside 1945-2026 (`ABSORPTION.md` §3).
    Those are parse artefacts, and scoring them as very old would be inventing a fact."""
    assert compute_factors(profile=profile, body="x" * 500, year=20092008).temporal is None


def test_temporal_is_reproducible_rather_than_wall_clock(profile) -> None:
    """! A factor that drifts with the clock cannot be part of a reproducible bundle."""
    a = compute_factors(profile=profile, body="x" * 500, year=2000, now=2026)
    b = compute_factors(profile=profile, body="x" * 500, year=2000, now=2026)

    assert a.temporal == b.temporal
    assert (
        a.temporal
        > compute_factors(profile=profile, body="x" * 500, year=1980, now=2026).temporal
    )


def test_an_annex_ranks_below_an_operative_article(profile) -> None:
    """Vera returns `LAMPIRAN / LAMPIRAN` hits above `Pasal` hits today; an annex is
    rarely the answer to a question about obligations."""
    pasal = compute_factors(profile=profile, body="x" * 500, article="Pasal 5")
    annex = compute_factors(profile=profile, body="x" * 500, article="LAMPIRAN I")

    assert pasal.structural == 1.0
    assert annex.structural == 0.2


def test_boilerplate_collapses_despite_high_authority(profile) -> None:
    """! `SCORING.md`'s warning made concrete: authority without relevance ranks the most
    prestigious document first for every query, and it looks correct. 30,520 rows of the
    incumbent are exactly `Cukup jelas.` — high-authority laws whose chunk says nothing."""
    f = compute_factors(
        profile=profile,
        body="Cukup jelas.",
        document_type="UNDANG-UNDANG",
        article="LAMPIRAN I",
        about="",
    )

    assert f.authority == 0.8, "the instrument really is a law"
    assert f.completeness == 0.0
    assert f.topical == 0.0
    assert f.structural == 0.2


def test_completeness_rewards_a_whole_provision_over_a_fragment(profile) -> None:
    clause = (
        "Setiap orang yang dengan sengaja melanggar ketentuan sebagaimana dimaksud "
        "dalam Pasal 5 ayat (2) dipidana dengan pidana denda paling banyak "
        "Rp50.000.000,00 dan wajib memulihkan kerusakan yang ditimbulkan."
    )
    fragment = compute_factors(profile=profile, body="Pasal 5")
    whole = compute_factors(profile=profile, body=clause)

    assert whole.completeness > fragment.completeness
    assert whole.legal_term_density > 0


def test_a_dense_fragment_does_not_beat_a_whole_provision(profile) -> None:
    """! Density is capped before it is mixed in. A two-word fragment that happens to be
    "wajib dilarang" is 100% legal terms and is still a fragment."""
    dense_fragment = compute_factors(profile=profile, body="wajib dilarang")

    assert dense_fragment.completeness == 0.0


def test_factors_stay_in_the_unit_interval(profile) -> None:
    """They multiply into `(1 + sum(w*f))`. A factor outside [0,1] silently rescales
    every weight fitted against it."""
    f = compute_factors(
        profile=profile,
        body="wajib " * 400,
        document_type="UNDANG-UNDANG DASAR",
        year=2026,
        article="Pasal 1",
        about="Ketentuan umum",
        now=2026,
    )

    for name, value in f.to_dict().items():
        assert 0.0 <= value <= 1.0, f"{name} = {value}"
