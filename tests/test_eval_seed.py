"""Guards on the imported seed set — the only ground truth either project has.

The set is checked in (`eval/id_legal/queries@v1.jsonl`), so these run hermetically: they
never reach for Vera's copy. What they assert is that the four lossy decisions in
`tools/import_vera_queries.py` still hold, because a silent regression in any of them
turns the set into something that scores a different thing than it claims to.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluate.labels import QuerySet, QueryType

SEED = Path(__file__).resolve().parent.parent / "eval" / "id_legal" / "queries@v1.jsonl"


@pytest.fixture(scope="module")
def seed() -> QuerySet:
    return QuerySet.load(SEED, version="v1-vera-seed")


def test_the_whole_set_loads(seed: QuerySet) -> None:
    assert len(seed) == 50


def test_no_relevance_carries_a_chunk_id(seed: QuerySet) -> None:
    """`EVAL.md` §1. `Relevance` has no field for one, so the only way an id could come
    back is inside `doc` or `locator` — a bare integer string is the shape to catch."""
    for query in seed:
        for judgement in query.relevant:
            assert not judgement.doc.strip().isdigit()
            assert not judgement.locator.strip().isdigit()


def test_every_document_is_a_short_form_citation(seed: QuerySet) -> None:
    """`UU 30/2007`, never `UNDANG-UNDANG 30 TAHUN 2007` — the form a query uses and the
    form the profile's `abbreviations` map produces."""
    for query in seed:
        for judgement in query.relevant:
            assert "/" in judgement.doc, judgement.doc
            assert judgement.doc.upper() != judgement.doc or judgement.doc.split()[0] in {
                "UU",
                "UUD",
                "PP",
            }, judgement.doc


def test_negatives_are_only_the_out_of_domain_cases(seed: QuerySet) -> None:
    """! The decision most likely to be undone by accident. `hard_negative` has a correct
    answer; only `out_of_domain` asserts the corpus cannot answer at all."""
    negatives = seed.of_type(QueryType.NEGATIVE)
    assert len(negatives) == 6
    for query in negatives:
        assert "vera_shape=out_of_domain" in query.notes
        assert not query.relevant


def test_the_hard_negatives_survived_as_answerable_queries(seed: QuerySet) -> None:
    hard = [q for q in seed if "vera_shape=hard_negative" in q.notes]
    assert len(hard) == 2
    for query in hard:
        assert query.type is QueryType.FACTUAL
        assert query.relevant
        # The discrimination test itself has no field yet; it must at least be recorded.
        assert "must_not_rank_first" in query.notes


def test_the_multi_tier_cases_keep_both_tiers(seed: QuerySet) -> None:
    """A parent law *and* the rule implementing it. Labelling only one made correct
    retrieval score as a miss — the defect the source set's own note records."""
    for query in seed.of_type(QueryType.CROSS_REFERENCE):
        assert len(query.documents) >= 2, query.qid


def test_every_exact_citation_case_carries_an_instrument_level_label(seed: QuerySet) -> None:
    """The query names a regulation, so the instrument itself must be labelled relevant.

    ! Not "and nothing else": q012 (`Undang-Undang Nomor 4 Tahun 2009 tentang
    pertambangan`) labels both the instrument and a specific clause within it, which is a
    correct label for a query that names a regulation and asks about part of it.
    """
    for query in seed.of_type(QueryType.EXACT_CITATION):
        assert any(not j.locator for j in query.relevant), query.qid


def test_the_original_question_shape_is_never_lost(seed: QuerySet) -> None:
    for query in seed:
        assert "vera_shape=" in query.notes


def test_the_file_is_utf8_jsonl_with_indonesian_text_intact(seed: QuerySet) -> None:
    lines = SEED.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 50
    assert all(json.loads(line)["qid"] for line in lines)
    assert any("Apakah" in query.query for query in seed)


def test_the_set_is_still_too_small_to_promote_on(seed: QuerySet) -> None:
    """Not a wish — a record. 50 queries is half `MIN_USEFUL_QUERIES`, and this test is
    here so that fact stays visible until the set is actually extended. When it starts
    failing, the set grew, and `CLAUDE.md`'s tracker line is what needs updating.
    """
    from evaluate.labels import check_size

    ok, detail = check_size(seed)
    assert not ok and "below 100" in detail
