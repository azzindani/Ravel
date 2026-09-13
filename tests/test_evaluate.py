"""Scoring: the coverage rule, the metrics, and the noise that has to travel with them.

The load-bearing test in this file is the coverage one. `EVAL.md` §1 says relevance is a
document plus a locator and never a chunk id, "and getting it wrong makes the eval set
worthless" — so most of what follows checks that a pasal-chunked corpus and an
ayat-chunked corpus can be scored against the same labels and come out comparable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluate import (
    Candidate,
    Coverage,
    EvalReport,
    LabeledQuery,
    Promotion,
    QuerySet,
    QueryType,
    Relevance,
    bootstrap_ci,
    check_composition,
    check_size,
    covers,
    evaluate,
    is_hit,
    locator_path,
    ndcg_at,
    read_promotions,
    recall_at,
    reciprocal_rank,
    routing_loss,
)
from spec import Registry


@pytest.fixture(scope="module")
def profile():
    return Registry.load().get("id_regulation")


def query(qid: str, kind: QueryType = QueryType.FACTUAL, **over) -> LabeledQuery:
    default = (
        () if kind is QueryType.NEGATIVE else (Relevance(doc="UU 36/2008", locator="Pasal 17"),)
    )
    relevant = over.pop("relevant", default)
    return LabeledQuery(qid=qid, query="q", type=kind, relevant=relevant, **over)


# -- the coverage rule -------------------------------------------------------------------


def test_the_locator_vocabulary_comes_from_the_profile(profile) -> None:
    """! `pasal`, `ayat`, `huruf` are Indonesian legal structure — corpus knowledge, so
    they are data (`CLAUDE.md` §12). Only the shape is code."""
    path = locator_path(profile, "BAB VI > Pasal 17 ayat (1) huruf a")

    assert path == (("bab", "vi"), ("pasal", "17"), ("ayat", "1"), ("huruf", "a"))


def test_a_pasal_chunk_covers_an_ayat_label(profile) -> None:
    """! The whole point. A pasal-chunked corpus must be scorable against ayat-level
    labels, or two chunkers cannot be compared at all."""
    assert covers(profile, "Pasal 17", "Pasal 17 ayat (1)") is Coverage.CONTAINS


def test_an_ayat_chunk_is_within_a_pasal_label(profile) -> None:
    assert covers(profile, "Pasal 17 ayat (1)", "Pasal 17") is Coverage.WITHIN


def test_the_wrong_ayat_covers_nothing(profile) -> None:
    """! A matcher written as "does the locator string contain the label" would call this
    a hit, and every recall number built on it would be inflated by exactly the chunks
    that are nearly right."""
    assert covers(profile, "Pasal 17 ayat (2)", "Pasal 17 ayat (1)") is Coverage.NONE


def test_an_exact_locator_is_exact(profile) -> None:
    assert covers(profile, "Pasal 17 ayat (1)", "Pasal 17 ayat (1)") is Coverage.EXACT


def test_a_document_level_label_is_covered_by_any_chunk_of_it(profile) -> None:
    """A weaker but legitimate label — some questions are answered by an instrument, not a
    clause. Scoring it as a miss on every arm would add noise and no information."""
    assert covers(profile, "Pasal 4", "") is Coverage.CONTAINS


def test_a_hit_requires_the_right_document(profile) -> None:
    candidate = Candidate(chunk_id="c1", doc="UU 28/2007", locator="Pasal 17 ayat (1)")

    assert not is_hit(profile, candidate, Relevance(doc="UU 36/2008", locator="Pasal 17"))


def test_citation_spelling_does_not_decide_a_hit(profile) -> None:
    """`UU No. 36/2008` and `UU 36/2008` are one instrument, and a label written by a
    human will not always match the parser's spelling."""
    candidate = Candidate(chunk_id="c1", doc="UU No. 36/2008", locator="Pasal 17")

    assert is_hit(profile, candidate, Relevance(doc="UU 36/2008", locator="Pasal 17"))


def test_strict_mode_refuses_a_narrower_chunk(profile) -> None:
    """Off by default: requiring exactness would make recall a function of chunk size
    rather than of retrieval quality."""
    candidate = Candidate(chunk_id="c1", doc="UU 36/2008", locator="Pasal 17 ayat (1)")
    judgement = Relevance(doc="UU 36/2008", locator="Pasal 17")

    assert is_hit(profile, candidate, judgement)
    assert not is_hit(profile, candidate, judgement, strict=True)


# -- the labeled set ---------------------------------------------------------------------


def test_a_negative_with_an_answer_is_a_contradiction() -> None:
    """! Refused rather than scored. One of the two facts is wrong, and either reading
    measures the label instead of the corpus."""
    with pytest.raises(ValueError, match="no answer in the corpus"):
        LabeledQuery(
            qid="q1",
            query="apa itu",
            type=QueryType.NEGATIVE,
            relevant=(Relevance(doc="UU 36/2008"),),
        )


def test_a_positive_with_no_answer_must_be_marked_negative() -> None:
    with pytest.raises(ValueError, match="mark it"):
        LabeledQuery(qid="q1", query="apa itu", type=QueryType.FACTUAL)


def test_there_is_nowhere_to_record_a_chunk_id() -> None:
    """! The rule made structural: a `Relevance` has no field for one."""
    with pytest.raises(TypeError):
        Relevance(doc="UU 36/2008", chunk_id="c1")  # type: ignore[call-arg]


def test_a_duplicate_qid_is_refused() -> None:
    with pytest.raises(ValueError, match="duplicate qid"):
        QuerySet(items=(query("q1"), query("q1")))


def test_a_small_set_is_reported_as_unreliable() -> None:
    """! Below roughly 100 queries a variant gets promoted on a coin flip."""
    ok, detail = check_size(QuerySet(items=tuple(query(f"q{i}") for i in range(50))))

    assert not ok
    assert "coin flip" in detail


def test_a_drifted_composition_is_reported() -> None:
    """A set that becomes 80% exact-citation measures the identifier path and is nearly
    blind to the embedder — while the headline number looks stable."""
    skewed = QuerySet(items=tuple(query(f"q{i}", QueryType.EXACT_CITATION) for i in range(100)))

    ok, detail = check_composition(skewed)

    assert not ok
    assert "exact_citation 100%" in detail


def test_the_holdout_is_stratified_and_seeded() -> None:
    """! A holdout that is accidentally all negatives measures one arm and certifies the
    rest (`EVAL.md` §4)."""
    kinds = (QueryType.FACTUAL, QueryType.EXACT_CITATION, QueryType.NEGATIVE)
    queries = QuerySet(
        items=tuple(query(f"{kind.value}-{i:03d}", kind) for kind in kinds for i in range(20))
    )

    sweep, held = queries.split(holdout=0.25, seed=1)
    again, _ = queries.split(holdout=0.25, seed=1)

    assert len(held) == 15 and len(sweep) == 45
    assert {q.qid for q in sweep} == {q.qid for q in again}
    assert len(held.of_type(QueryType.NEGATIVE)) == 5, "one type did not dominate"


def test_a_set_round_trips_through_jsonl(tmp_path: Path) -> None:
    queries = QuerySet(items=(query("q1"), query("q2", QueryType.NEGATIVE)), version="v1")
    path = tmp_path / "queries.jsonl"
    queries.save(path)

    assert QuerySet.load(path).items == queries.items


def test_a_hand_written_json_array_loads_without_reformatting(tmp_path: Path) -> None:
    """The seed set is a JSON array. Reformatting somebody's hand-labelled ground truth to
    suit a parser is a chance to corrupt it for no gain."""
    path = tmp_path / "queries.json"
    path.write_text(
        json.dumps(
            [
                {
                    "qid": "q1",
                    "query": "Berapa tarif PPh?",
                    "type": "factual",
                    "relevant": [{"doc": "UU 36/2008", "locator": "Pasal 17", "grade": 3}],
                }
            ]
        ),
        encoding="utf-8",
    )

    loaded = QuerySet.load(path)

    assert loaded.items[0].relevant[0].grade == 3


# -- the metrics -------------------------------------------------------------------------


def test_recall_counts_judgements_not_hits() -> None:
    """! Four chunks of the same pasal in the top five is one judgement covered. Counting
    hits would reward a chunker for producing more rows."""
    assert recall_at([3, 3, 3, 3, 0], relevant_count=1, k=5) == 1.0
    assert recall_at([3, 0, 0], relevant_count=2, k=3) == 0.5


def test_reciprocal_rank_and_ndcg_reward_position() -> None:
    assert reciprocal_rank([0, 0, 3]) == pytest.approx(1 / 3)
    assert reciprocal_rank([0, 0, 0]) == 0.0
    assert ndcg_at([3, 1]) > ndcg_at([1, 3])


def test_a_query_the_variant_crashed_on_scores_zero(profile) -> None:
    """! Never silently skipped. A variant that failed on 30 queries would otherwise
    report a higher mean than one that answered them badly."""
    queries = QuerySet(items=(query("q1"), query("q2")))

    report = evaluate(
        queries,
        {"q1": [Candidate("c1", "UU 36/2008", "Pasal 17")]},
        profile=profile,
        variant="v",
    )

    assert report.recall[50] == 0.5


def test_a_negative_query_scores_on_returning_nothing(profile) -> None:
    queries = QuerySet(items=(query("n1", QueryType.NEGATIVE), query("n2", QueryType.NEGATIVE)))

    report = evaluate(
        queries,
        {"n2": [Candidate("c9", "UU 36/2008", "Pasal 1")]},
        profile=profile,
        variant="v",
    )

    assert report.negative_empty_rate == 0.5


def test_exact_citation_recall_is_reported_separately(profile) -> None:
    """Below 100% it is a bug, not a tuning result (`EVAL.md` §2), so it cannot be allowed
    to average away inside the headline."""
    queries = QuerySet(
        items=(
            query("e1", QueryType.EXACT_CITATION),
            query("e2", QueryType.EXACT_CITATION),
            query("f1"),
        )
    )

    report = evaluate(
        queries,
        {
            "e1": [Candidate("c1", "UU 36/2008", "Pasal 17")],
            "e2": [Candidate("c2", "UU 28/2007", "Pasal 9")],
            "f1": [Candidate("c3", "UU 36/2008", "Pasal 17 ayat (1)")],
        },
        profile=profile,
        variant="v",
    )

    assert report.exact_citation_recall == 0.5
    assert report.recall[50] == pytest.approx(2 / 3)


def test_two_chunkings_of_the_same_corpus_score_comparably(profile) -> None:
    """! The reason the coverage rule exists. The same labels, one corpus chunked at pasal
    and one at ayat, and both retrieve the answer."""
    queries = QuerySet(items=(query("q1"),))
    by_pasal = {"q1": [Candidate("a", "UU 36/2008", "Pasal 17")]}
    by_ayat = {"q1": [Candidate("b", "UU 36/2008", "Pasal 17 ayat (1)")]}

    pasal = evaluate(queries, by_pasal, profile=profile, variant="pasal")
    ayat = evaluate(queries, by_ayat, profile=profile, variant="ayat")

    assert pasal.recall[50] == ayat.recall[50] == 1.0


# -- noise and promotion --------------------------------------------------------------------


def test_a_confidence_interval_brackets_the_mean() -> None:
    scores = [1.0] * 60 + [0.0] * 40
    low, high = bootstrap_ci(scores, seed=7)

    assert low < 0.6 < high
    assert bootstrap_ci([], seed=7) == (0.0, 0.0)


def test_an_overlapping_interval_is_not_a_win() -> None:
    """! `EVAL.md` §4: a 1.5-point Recall@50 difference on 200 queries is usually noise,
    and treating it as signal is how a corpus gets tuned to randomness."""
    challenger = EvalReport(
        variant="new",
        query_set_version="v1",
        mode="oracle",
        queries=200,
        recall={50: 0.72},
        recall_ci={50: (0.66, 0.78)},
    )
    incumbent = EvalReport(
        variant="old",
        query_set_version="v1",
        mode="oracle",
        queries=200,
        recall={50: 0.705},
        recall_ci={50: (0.64, 0.77)},
    )

    ok, detail = challenger.beats(incumbent)

    assert not ok
    assert "noise, not a result" in detail


def test_a_disjoint_interval_is_a_win() -> None:
    challenger = EvalReport(
        variant="new",
        query_set_version="v1",
        mode="oracle",
        queries=200,
        recall={50: 0.85},
        recall_ci={50: (0.81, 0.89)},
    )
    incumbent = EvalReport(
        variant="old",
        query_set_version="v1",
        mode="oracle",
        queries=200,
        recall={50: 0.70},
        recall_ci={50: (0.65, 0.75)},
    )

    assert challenger.beats(incumbent)[0]


def test_numbers_from_different_query_sets_refuse_to_be_compared() -> None:
    """! A set that changed between two runs cannot compare them, and the two numbers
    would otherwise sit in the same table looking alike."""
    a = EvalReport(variant="a", query_set_version="v1", mode="oracle", queries=10)
    b = EvalReport(variant="b", query_set_version="v2", mode="oracle", queries=10)

    ok, detail = a.beats(b)

    assert not ok
    assert "not comparable" in detail


def test_oracle_and_routed_numbers_refuse_to_be_compared() -> None:
    a = EvalReport(variant="a", query_set_version="v1", mode="oracle", queries=10)
    b = EvalReport(variant="a", query_set_version="v1", mode="routed", queries=10)

    assert not a.beats(b)[0]


def test_routing_loss_points_at_the_right_stage() -> None:
    """! `EVAL.md` §3: reporting one number without the other has repeatedly sent people
    to re-chunk when their real problem was routing."""
    oracle = EvalReport(
        variant="v", query_set_version="v1", mode="oracle", queries=100, recall={50: 0.90}
    )
    routed = EvalReport(
        variant="v", query_set_version="v1", mode="routed", queries=100, recall={50: 0.62}
    )

    loss = routing_loss(oracle, routed)

    assert loss["routing_loss"] == pytest.approx(0.28)
    assert "clusters_probed" in loss["verdict"]


def test_a_promotion_is_appended_never_rewritten(tmp_path: Path) -> None:
    """! The history is the artifact. A file holding only the current winner answers "what
    is it now" and not "why"."""
    path = tmp_path / "eval" / "promotions.jsonl"
    for i, variant in enumerate(("token512", "pasal")):
        Promotion(
            variant=variant,
            config_hash=f"h{i}",
            bundle_version=f"v{i}",
            eval_run=f"run{i}",
            query_set_version="v1",
            metrics={"recall@50": 0.7 + i * 0.1},
            rationale="beat the incumbent outside the interval",
        ).append_to(path)

    records = read_promotions(path)

    assert [r["variant"] for r in records] == ["token512", "pasal"]
    assert read_promotions(tmp_path / "missing.jsonl") == ()
